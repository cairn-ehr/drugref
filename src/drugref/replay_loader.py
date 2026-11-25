"""The replay loader: inserts a release's exported curated rows (#259).

Split out of `release_replay` to keep that file under the ~500-line cap. The
loader is the part the curated export grows with, so it lives alone with the
table constants (`_TIMED`, `_RULINGS`, `_REACH`, `_SNAPSHOT`, `_OBSERVATIONS`) it
is driven by; `release_replay` imports them back (tests patch `release_replay.*`).
"""
from psycopg import sql
from psycopg.types.json import Jsonb

from drugref import graded_pairs, overlay, release_archive

# Exported tables with a timestamp: (primary key, when the row was written). The
# ORDER is the load order for rows written at one instant (one transaction stamps
# one `now()`): every table after the ones its foreign keys name. The reach and
# snapshot tables have no timestamp: they load with their ruling. Not handled: the
# self-FK `reviewer_account.created_by` -- two accounts created in ONE transaction
# tie and load by uuid order (the reviewer-service creates one per transaction).
_TIMED = {"reviewer_account": ("reviewer_uuid", "created_at"),
          "reviewer_profile": ("reviewer_profile_id", "recorded_at"),
          "signing_key": ("signing_key_id", "registered_at"),
          "reviewer_key_enrolment": ("reviewer_key_enrolment_id", "enrolled_at"),
          "curated_interaction": ("curated_interaction_id", "reviewed_at"),
          "curated_condition": ("curated_condition_id", "reviewed_at"),
          "curated_class_interaction": ("curated_class_interaction_id", "reviewed_at"),
          "curated_property_ruling": ("curated_property_ruling_id", "reviewed_at"),
          "class_expansion_policy": ("policy_id", "reviewed_at"),
          # No FK to its target or its key (db/030), so it may follow any of them.
          # A RELEASE's signature is not loaded (`_signs_a_release`).
          "assertion_signature": ("signature_id", "recorded_at"),
          "reviewer_annotation": ("reviewer_annotation_id", "recorded_at"),
          "reviewer_evidence_reference": ("reviewer_evidence_reference_id",
                                          "recorded_at"),
          # After the rulings: an ack names an episode a ruling opened (db/083).
          # Loaded onto the REPLAYED episode of its (key, pair) (`_ack_episode`).
          "graded_pair_departure_ack": ("ack_id", "acknowledged_at")}
_RULINGS = "curated_class_interaction"
_REACH = "curated_class_interaction_reach"
_SNAPSHOT = "curated_class_interaction_snapshot"
# Compared, never loaded: db/083's triggers regenerate observations in the target
# as rulings load and runs finish (`compare_observations`).
_OBSERVATIONS = "graded_pair_observation"
_ACKS = "graded_pair_departure_ack"
# An observation's (ruling key, pair): what both sides of the comparison match on.
_KEY_PAIR = ("rule_grain", "subject_key", "object_class_uuid", "relationship",
             "subject_moiety", "partner_moiety")
assert (set(_TIMED) | {_REACH, _SNAPSHOT, _OBSERVATIONS}
        == set(release_archive._EXPORT_TABLES))


def _columns(conn, table) -> list[str]:
    """Insertable columns (generated ones are computed by the database)."""
    return [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = "
        "'drugref' AND table_name = %s AND is_generated = 'NEVER' "
        "ORDER BY ordinal_position", (table,))]


class _Loader:
    """Inserts exported rows verbatim (ids included) and replays supersession."""

    def __init__(self, conn, export, report):
        self.conn, self.report = conn, report
        self.cols = {t: _columns(conn, t) for t in release_archive._EXPORT_TABLES}
        # Tables that supersede: their rows MUST carry the pointer (a missing one
        # is a damaged export, never "live"); insert-only tables have none.
        self.superseding = {t for t in _TIMED if "superseded_by" in self.cols[t]}
        # Rows a migration seeds (expansion policies) are already in the fresh
        # target: never re-inserted (only compared, `_compare_seeded`), but a
        # curator's later row may supersede them.
        # Read through to_jsonb so ids compare as the export spells them (a uuid key
        # is a string there).
        self.seeded = {t: {r[0] for r in conn.execute(sql.SQL(
            "SELECT to_jsonb({pk}) FROM drugref.{t}").format(
            pk=sql.Identifier(pk), t=sql.Identifier(t)))}
            for t, (pk, _) in _TIMED.items()}
        self.loaded = {t: set(ids) for t, ids in self.seeded.items()}
        self.compared: dict[str, set] = {t: set() for t in _TIMED}   # seeded, seen
        # superseder id -> ids it superseded, per table that supersedes at all
        self.points: dict[str, dict[int, list[int]]] = {t: {} for t in _TIMED}
        for t, (pk, _) in _TIMED.items():
            for row in export[t] if t in self.superseding else []:
                if row["superseded_by"] is not None:
                    self.points[t].setdefault(row["superseded_by"], []).append(row[pk])
        self.reach: dict[int, list[dict]] = {}
        for row in export[_REACH]:
            self.reach.setdefault(row["curated_class_interaction_id"], []).append(row)
        self.snapshot = {r["curated_class_interaction_id"]: r
                         for r in export[_SNAPSHOT]}
        self.observations = {r["observation_id"]: r for r in export[_OBSERVATIONS]}
        self.stamp = None     # the last loaded row's timestamp (`load`)

    def _insert(self, table, row) -> None:
        cols = sql.SQL(", ").join(map(sql.Identifier, self.cols[table]))
        self.conn.execute(
            sql.SQL("INSERT INTO drugref.{t} ({c}) OVERRIDING SYSTEM VALUE "
                    "SELECT {c} FROM jsonb_populate_record(NULL::drugref.{t}, %s)"
                    ).format(t=sql.Identifier(table), c=cols), (Jsonb(row),))

    def load(self, table, row) -> None:
        pk, stamp = _TIMED[table]
        # A new timestamp is a new origin transaction, so the previous one had
        # COMMITTED: fire the refreshes it queued now, as its commit did. Left to
        # the batch's one commit, they would see only the batch's final state (a
        # ruling withdrawn later in the batch would never be observed).
        if row[stamp] != self.stamp:
            self._commit_point()
            self.stamp = row[stamp]
        rid = row[pk]
        sup = row["superseded_by"] if table in self.superseding else None
        if rid in self.seeded[table]:
            self.compared[table].add(rid)
            self._compare_seeded(table, row)
            return
        # Point at the superseder only if it is already in; otherwise it points
        # back at this row when it loads.
        if sup is not None and sup not in self.loaded[table]:
            row = dict(row, superseded_by=None)
        if table == _ACKS:
            episode = self._ack_episode(row)
            if episode is None:
                self.report.differences.append(
                    f"acknowledgement {rid} has no open episode in the replay")
                return
            row = dict(row, observation_id=episode)
        self._insert(table, row)
        self.loaded[table].add(rid)
        earlier = [o for o in self.points[table].get(rid, [])
                   if o in self.loaded[table]]
        if earlier:
            overlay.point_rows(self.conn, table, pk, rid, earlier)
        self.report.curated_rows += 1
        if table == _RULINGS:
            self._ruling_reach(rid)

    def _commit_point(self) -> None:
        """Run the db/083 refreshes queued so far, as COMMIT would, without
        committing (switching their deferred triggers to IMMEDIATE fires them)."""
        names = ", ".join(graded_pairs.REFRESH_TRIGGERS)
        self.conn.execute(f"SET CONSTRAINTS {names} IMMEDIATE")
        self.conn.execute(f"SET CONSTRAINTS {names} DEFERRED")

    def _ack_episode(self, ack) -> int | None:
        """The replayed observation an exported ack closes: the OPEN episode (no
        ack yet) of the same (key, pair) as the one it named in the origin, the
        latest if several. None when the export lacks that observation or the
        replay has no such episode open: reported, the ack is not loaded."""
        named = self.observations.get(ack["observation_id"])
        if named is None:
            return None
        found = self.conn.execute(
            f"SELECT o.observation_id FROM {graded_pairs.OBSERVATION_TABLE} o "
            "WHERE (o.rule_grain, o.subject_key::text, o.object_class_uuid::text, "
            "o.relationship, o.subject_moiety::text, o.partner_moiety::text) "
            "= (%s, %s, %s, %s, %s, %s) "
            f"AND NOT EXISTS (SELECT 1 FROM {graded_pairs.ACK_TABLE} a "
            "WHERE a.observation_id = o.observation_id) "
            "ORDER BY o.first_seen_at DESC, o.observation_id DESC LIMIT 1",
            [named[c] for c in _KEY_PAIR]).fetchone()
        return None if found is None else found[0]

    def compare_observations(self) -> None:
        """After the last commit: each (key, pair) the export observed and the
        replay did not, or the reverse, is one difference (spec 5.6). An export
        holding db/083's backfill cannot match (the replay migrates an empty
        database, so its backfill wrote nothing): warn and skip instead."""
        if any(o["captured"] == "backfill_db083" for o in self.observations.values()):
            self.report.warnings.append("observations predate db/083: not reproducible")
            return
        origin = {tuple(o[c] for c in _KEY_PAIR) for o in self.observations.values()}
        replay = set(self.conn.execute(
            "SELECT " + ", ".join(f"{c}::text" for c in _KEY_PAIR)
            + f" FROM {graded_pairs.OBSERVATION_TABLE}"))
        for side, extra in (("the origin but not the replay", origin - replay),
                            ("the replay but not the origin", replay - origin)):
            self.report.differences += [f"graded pair observed in {side}: "
                                        + " ".join(k) for k in sorted(extra)]

    def _compare_seeded(self, table, row) -> None:
        """A row a migration already seeded is not re-inserted: report it when the
        exported copy differs (#277). Compared as typed values in SQL, so a time
        zone or JSON spelling never reads as a difference. Two columns are left
        out: the timestamp (the migration stamped it when IT ran) and
        `superseded_by` (repointed when its superseder loads)."""
        pk, stamp = _TIMED[table]
        cols = [c for c in self.cols[table] if c not in (stamp, "superseded_by")]

        def of(alias):
            return sql.SQL(", ").join(sql.Identifier(alias, c) for c in cols)
        differs = self.conn.execute(sql.SQL(
            "SELECT ROW({t}) IS DISTINCT FROM ROW({e}) FROM drugref.{table} t, "
            "jsonb_populate_record(NULL::drugref.{table}, %s) e "
            "WHERE t.{pk} = e.{pk}").format(
            t=of("t"), e=of("e"), table=sql.Identifier(table),
            pk=sql.Identifier(pk)), (Jsonb(row),)).fetchone()[0]
        if differs:
            self.report.differences.append(
                f"{table} {row[pk]}: the exported row differs from the one a "
                "migration seeded")

    def _ruling_reach(self, rid) -> None:
        """Load a ruling's reach rows; compare a ruling-time snapshot with now."""
        rows = self.reach.get(rid, [])
        marker = self.snapshot.get(rid)
        if marker is not None and marker["captured"] == "at_ruling":
            self.report.rulings_checked += 1
            for side in ("subject", "partner"):
                want = {r["moiety_uuid"] for r in rows if r["side"] == side}
                got = {str(m) for (m,) in self.conn.execute(
                    "SELECT moiety_uuid FROM drugref.class_rule_member "
                    "WHERE curated_class_interaction_id = %s AND side = %s",
                    (rid, side))}
                if want != got:
                    self.report.differences.append(
                        f"class ruling {rid}: {side} reach differs from ruling time: "
                        f"{len(got - want)} added, {len(want - got)} missing")
        for r in rows:
            self._insert(_REACH, r)
        if marker is not None:
            self._insert(_SNAPSHOT, marker)

    def unmatched(self) -> None:
        """After the last row: report what the export names but never supplied
        (#278). A row pointing at a superseder the export lacks loaded live, though
        the origin has it superseded; a seeded row absent from it was never compared."""
        for t in _TIMED:
            for sup in sorted(self.points[t].keys() - self.loaded[t]):
                self.report.differences.append(
                    f"{t} {self.points[t][sup]}: superseded by {sup}, which the "
                    "export lacks")
            for rid in sorted(self.seeded[t] - self.compared[t]):
                self.report.differences.append(
                    f"{t} {rid}: a migration seeded it, but the export lacks it")

    def reset_sequences(self) -> None:
        """Move each identity past the loaded ids, so a later write cannot collide.
        A uuid key (reviewer_account) has no sequence and is skipped. Observations
        need none: the target wrote its own, from its own sequence."""
        for table, (pk, _) in _TIMED.items():
            if self.conn.execute("SELECT pg_get_serial_sequence(%s, %s)",
                                 (f"drugref.{table}", pk)).fetchone()[0] is None:
                continue
            self.conn.execute(sql.SQL(
                "SELECT setval(pg_get_serial_sequence({q}, {p}), max({pk})) "
                "FROM drugref.{t} HAVING max({pk}) IS NOT NULL").format(
                q=sql.Literal(f"drugref.{table}"), p=sql.Literal(pk),
                pk=sql.Identifier(pk), t=sql.Identifier(table)))
