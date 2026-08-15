# tests/test_curation_orphans.py
"""The orphan detector's CONSUMER (issue 76).

`db/029` shipped `drugref.curated_target_unresolved` -- live curated rows whose
candidate is no longer projected after a per-source rebuild -- and NOTHING READ IT.

That is the exact failure `interactions.unresolved_expansion_policy`'s own docstring
records this project having already made once: db/010 shipped
`expansion_policy_unresolved` with no consumer at all, "which is precisely the failure
mode it was written to catch." A detector nobody calls is not a detector. An orphaned
judgement would sit in the database being reported to no one, forever, and the whole
reason the view exists -- an operator must be TOLD when a rebuild leaves curator
judgement pointing at nothing -- goes unmet.

WHY THE READ LIVES IN curation.py AND NOT IN cli.py. cli.py's module docstring makes
the rule: a handler must not embed SQL against curated, append-only tables, because
`test_only_the_current_view_reads_the_policy_table_directly` finds readers through
pg_rewrite, which sees views and matviews and CANNOT see a query embedded in Python.
`_handle_status` is a stated exception for `loaded_release` and `ingest_run_incomplete`
-- operational views, not curated data -- and that exception does NOT stretch to cover
the curated overlay. So the read is a function in the module that owns the curated
write path, exactly as `unresolved_expansion_policy` sits in `interactions.py`, and the
handler calls it.
"""
import psycopg
import pytest

from drugref import curation


def test_no_orphans_when_the_candidate_is_still_projected(conn, a_graded_rule):
    """The control, and the normal state: expected EMPTY on a healthy database."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    assert curation.unresolved_targets(conn) == []


def test_an_orphaned_interaction_judgement_is_returned(conn, a_graded_rule):
    """A rebuild that drops the candidate must surface the judgement pointing at it.

    `DELETE FROM class_contraindication` is exactly what a per-source re-ingest does
    before rewriting the projection, so this is the ordinary sequence rather than a
    contrived one.
    """
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    conn.execute("DELETE FROM drugref.class_contraindication")

    # ALL SEVEN FIELDS, because the stub-driven CLI tests below cannot see a
    # column-order mistake -- they supply a tuple already in the assumed order. Swapping
    # object_uuid with relationship, or reviewed_by with reviewed_against, would leave
    # every other assertion in this file passing.
    #
    # THE RATIONALE USED TO SAY "built positionally from the SELECT
    # (`UnresolvedTarget(*row)`)". It is not, and has not been since the record started
    # binding by NAME through `_UNRESOLVED_COLUMNS` -- which is what makes a transposed
    # SELECT structurally impossible rather than merely tested-for. The assertion still
    # earns its place (it pins the VALUES, and the view's arms are hand-written SQL
    # that can still put the wrong column in the right slot); only the reason was
    # stale, and it was stale in a comment db/035 edited without re-reading.
    assert curation.unresolved_targets(conn) == [
        curation.UnresolvedTarget(
            target_table="curated_interaction",
            subject_moiety=a_graded_rule["subject"],
            object_uuid=a_graded_rule["class"],
            relationship="CI_MoA",
            reviewed_by="test",
            reviewed_against="2026.07.06",
            # NULL on this arm: db/035's third arm carries the CLASS grain's subject
            # here and leaves subject_moiety NULL, and the two must not be confused.
            subject_class=None)]


def test_a_rekeyed_candidate_orphans_the_judgement(conn, a_graded_rule):
    """The REALISTIC orphan, and the one the whole view is named for: upstream
    re-projects the same (moiety, class) pair under a DIFFERENT relationship.

    The two tests above delete the candidate table wholesale, which exercises only the
    all-or-nothing case -- and against that case the view's `cc.relationship =
    c.relationship` predicate is dead weight, because with the table empty the NOT
    EXISTS holds whatever it compares. Mutating that predicate to `true` survived the
    whole suite. A partial mismatch is what a MED-RT re-key actually looks like
    (`unresolved_targets`' own docstring says "a candidate upstream has re-keyed"), and
    it is the only shape that can tell the predicate apart from a constant.
    """
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    # The rebuild: same subject, same class, same source -- only the predicate moved.
    conn.execute(
        "UPDATE drugref.class_contraindication SET relationship = 'CI_PE'")

    orphans = curation.unresolved_targets(conn)
    assert len(orphans) == 1, (
        "the candidate row still exists but no longer carries CI_MoA, so the judgement "
        "resolves to nothing -- the view must compare the relationship, not merely the "
        "(subject, object) pair")
    assert orphans[0].relationship == "CI_MoA"


def test_an_orphaned_condition_ruling_is_returned(conn, a_contradicted_pair):
    """The UNION ALL's second arm. Both candidate tables must be gone before a
    condition ruling counts as orphaned, because either one alone keeps it resolved."""
    curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="test", reviewed_against="2026.07.06")
    conn.execute("DELETE FROM drugref.moiety_condition_contraindication")
    assert curation.unresolved_targets(conn) == [], (
        "one surviving candidate table still resolves the ruling")

    conn.execute("DELETE FROM drugref.moiety_condition_indication")
    # ALL SEVEN FIELDS on THIS arm too, for the reason given on the interaction test
    # above (including what that comment says about its own stale rationale).
    # above. The two arms of a UNION ALL are two independent column lists, and pinning
    # only one leaves the other free to transpose: swapping reviewed_by with
    # reviewed_against in the second arm alone survived the whole suite while the first
    # arm's assertion stayed green.
    assert curation.unresolved_targets(conn) == [
        curation.UnresolvedTarget(
            target_table="curated_condition",
            subject_moiety=a_contradicted_pair["moiety"],
            object_uuid=a_contradicted_pair["condition"],
            relationship=None,
            reviewed_by="test",
            reviewed_against="2026.07.06",
            subject_class=None)], (
        "a condition ruling is keyed on the PAIR and carries no relationship")


def test_a_superseded_judgement_is_not_an_orphan(conn, a_graded_rule):
    """Only LIVE rows. A corrected judgement's superseded predecessor still names the
    old candidate, and reporting it would make every correction look like breakage.

    THE CANDIDATE MUST BE DELETED FOR THIS TEST TO MEAN ANYTHING. An earlier version
    stopped after the correction and asserted the result was empty -- but with the
    candidate still projected BOTH rows resolve, so the empty result was
    over-determined and the assertion held whether or not the view filtered on
    `superseded_by`. Deleting `WHERE c.superseded_by IS NULL` from both arms of db/029's
    view survived the entire suite. Orphaning the candidate is what forces the
    distinction: two rows now name a candidate that is gone, and exactly one of them is
    live.
    """
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="test",
        reviewed_against="2026.07.06")
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="minor", evidence_grade="probable", reviewed_by="test",
        reviewed_against="2026.08.01")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    conn.execute("DELETE FROM drugref.class_contraindication")

    orphans = curation.unresolved_targets(conn)
    assert len(orphans) == 1, (
        "both rows name the vanished candidate, but only the LIVE one is an orphan -- "
        "reporting the superseded predecessor as well would make every correction look "
        "like breakage")
    assert orphans[0].reviewed_against == "2026.08.01", (
        "the row reported must be the CORRECTION, not the predecessor it replaced")


# ---- the CLI consumer -------------------------------------------------------


class _Conn:
    """A stub connection returning canned rows per query, for the DB-free CLI tests.

    tests/test_cli.py drives `_handle_status` the same way; the handler is a dispatch
    thin enough that a stub is a truer test of its OUTPUT than a live database would be.
    """

    def __init__(self, orphans=(), raises=None, absent=(), applied=False):
        self._orphans = list(orphans)
        # {sql fragment: exception} -- an UNDER-MIGRATED database, which is the one
        # state a stub models better than a real one. Building a db/034 database to
        # test a db/035 guard would mean a second migration ledger in the fixtures;
        # the guard's whole job is turning one psycopg class into one sentence, and
        # that is exactly what a raising stub exercises.
        self._raises = dict(raises or {})
        # ISSUE 122'S TWO PROBES. A guard may no longer assert a cause it has not
        # confirmed, so it now asks `to_regclass` which relations are really gone and
        # asks the ledger whether their migration is recorded applied. `absent` and
        # `applied` are those two answers.
        #
        # ⇒ AND THE INTERESTING STATE CANNOT BE BUILT ON THE REAL TEST DATABASE, which
        # is why this stub grew rather than being replaced by a fixture. "DROPPED"
        # means absent WHILE its migration is applied, so producing it for real means
        # committing a DROP to the session-scoped migrated database -- breaking every
        # test that runs after it. The pieces are each tested where they can be tested
        # honestly: `guard_message`'s four states purely (test_migration_guard.py), the
        # two probes against a live database (test_db.py), and the wiring here.
        self._absent = set(absent)
        self._applied = applied

    def rollback(self):
        """`db.missing_relations` rolls back before probing, because the failed
        statement aborted the transaction. A stub without this raises AttributeError
        from inside the guard -- which is precisely the class of failure issue 122 is
        about, so it is modelled rather than ignored."""

    def execute(self, sql, params=None):
        self._last = sql
        self._params = params
        for fragment, exc in self._raises.items():
            if fragment in sql:
                raise exc
        return self

    def fetchall(self):
        if "curated_target_unresolved" in self._last:
            return self._orphans
        return []

    def fetchone(self):
        """Zero, for every scalar count `_handle_status` asks for.

        db/035's class-grain block reads three counts this way. A stub that returned
        rows here would be asserting the class grain's OWN output, which
        tests/test_class_grain_detectors.py does against a real database -- these
        tests exist to pin the ORPHAN block's rendering, and a stub is a truer test of
        that than a live database only while it stays a stub.

        THE TWO EXCEPTIONS ARE ISSUE 122'S PROBES, which are not counts: `to_regclass`
        answers NULL for an absent relation, and the ledger answers a boolean. A stub
        returning 0 to both would report every relation PRESENT and every migration
        UNAPPLIED -- a state that cannot exist, and one that would send every guard down
        the same branch no matter what the test meant to model.
        """
        if "to_regclass" in self._last:
            return (None,) if self._params[0] in self._absent else ("an_oid",)
        if "schema_migration" in self._last:
            return (self._applied,)
        return (0,)


def test_status_reports_no_orphans_on_a_healthy_database(capsys):
    """`none` rather than a bare header, matching the two blocks already there: a
    header with nothing under it reads as output that got cut off, not as an answer."""
    from drugref import cli

    assert cli._handle_status(_Conn(), None) == 0
    assert "unresolved curated targets: none" in capsys.readouterr().out


def test_status_reports_an_orphan_loudly(capsys):
    """THE POINT OF THE WHOLE ISSUE. An orphan must be visible in the one command an
    operator runs to ask "is this database healthy?", and must not be reported in the
    same neutral voice as a loaded release -- it means a curator's judgement now points
    at nothing.

    THE BANNER IS ASSERTED VERBATIM, and that is the whole difference between this test
    and a count check. "Loudly" is the word in the name; deleting the `**` marker left
    the count, the table and the curator all still printed, so an earlier version of
    this test passed with the loudness removed. Every field of the detail line is
    asserted for the same reason -- collapsing the format string to just the table and
    the curator also survived.
    """
    from drugref import cli

    row = ("curated_interaction", "11111111-1111-5111-8111-111111111111",
           "22222222-2222-5222-8222-222222222222", "CI_MoA", "ahoward", "2026.07.06",
           None)
    assert cli._handle_status(_Conn([row]), None) == 0
    out = capsys.readouterr().out
    assert "unresolved curated targets: 1" in out
    assert "** a rebuild left curator judgement pointing at nothing **" in out
    # Whitespace-normalised so the column padding stays free to change, while every
    # FIELD stays pinned -- dropping object_uuid, relationship or reviewed_against from
    # the format string is the mutation this has to kill.
    assert ("curated_interaction 11111111-1111-5111-8111-111111111111 -> "
            "22222222-2222-5222-8222-222222222222 [CI_MoA] reviewed by ahoward "
            "against 2026.07.06") in " ".join(out.split())


def test_status_renders_a_condition_orphan_without_a_relationship(capsys):
    """The `relationship is None` arm of the detail line, which no other test reaches.

    A condition ruling is keyed on the pair and carries no predicate, so this is the
    branch every `curated_condition` orphan will take at runtime -- and until this test
    it had never executed. The bracket must be absent entirely rather than rendered
    empty or as the string "None".
    """
    from drugref import cli

    row = ("curated_condition", "11111111-1111-5111-8111-111111111111",
           "33333333-3333-5333-8333-333333333333", None, "ahoward", "2026.07.06",
           None)
    assert cli._handle_status(_Conn([row]), None) == 0
    out = capsys.readouterr().out
    assert ("curated_condition 11111111-1111-5111-8111-111111111111 -> "
            "33333333-3333-5333-8333-333333333333 reviewed by ahoward "
            "against 2026.07.06") in " ".join(out.split())
    assert "None" not in out and "[]" not in out


def test_status_renders_a_class_grain_orphans_OWN_subject(capsys):
    """THE THIRD ARM'S DETAIL LINE, and the half of issue #90 that db/035 left open.

    `subject_moiety` is NULL for EVERY row of the class-grain arm (db/035's third arm
    hardcodes the literal), so a renderer reading only that column prints the string
    "None" where the rule's subject belongs -- and since two class rules can share an
    object and an axis, three DISTINCT orphaned rules render as three identical lines.
    The operator is told a judgement is orphaned and not which one, which is the same
    "reported to nobody" the whole migration is named for, one layer further out.

    THE TWO SIBLING TESTS ABOVE COULD NOT CATCH THIS: both pass `subject_class=None`,
    so the fallback branch never executed. `test_the_status_reader_carries_the_class
    _subject` (tests/test_class_grain_detectors.py) asserts the READER carries the
    column and stops there -- its own docstring, "a third arm the reader does not
    project is a detector that still reports nothing", applies one layer past where it
    checks.

    ASSERTS THE ABSENCE OF "None" AS WELL AS THE PRESENCE OF THE UUID, because
    printing both columns would satisfy a presence-only assertion while still putting
    a literal "None" in an operator's face.
    """
    from drugref import cli

    row = ("curated_class_interaction", None,
           "22222222-2222-5222-8222-222222222222", "CI_MoA", "ahoward", "2026.07.06",
           "44444444-4444-5444-8444-444444444444")
    assert cli._handle_status(_Conn([row]), None) == 0
    out = capsys.readouterr().out
    assert ("curated_class_interaction 44444444-4444-5444-8444-444444444444 -> "
            "22222222-2222-5222-8222-222222222222 [CI_MoA] reviewed by ahoward "
            "against 2026.07.06") in " ".join(out.split())
    assert "None" not in out


def test_a_database_predating_db035_names_the_migration_rather_than_tracebacking(
        capsys):
    """THE GUARD'S SIBLING FAILURE, and db/035 walked straight into it.

    The guard beside this catches `UndefinedTable` -- correct for db/029, when the
    whole VIEW was what a stale database lacked. db/035 widened the SELECT with
    `subject_class`, so a database that HAS the view but predates db/035 fails with
    `UndefinedColumn` instead, which is a sibling of `UndefinedTable` under
    `ProgrammingError` and NOT a subclass: the guard does not fire, and `main` catches
    only RuntimeError/ChainError/NoLiveDecisionError, so psycopg's traceback reaches
    the operator after two blocks of real answers.

    That state is not exotic -- it is EVERY existing deployment between pulling this
    code and running `drugref migrate`, and `drugref status` is the command an operator
    runs first. THE STANDING RULE THIS TEST WRITES DOWN: a migration that widens a view
    a guarded block reads must widen that block's exception tuple in the same commit.
    """
    from drugref import cli

    conn = _Conn(raises={"curated_target_unresolved": psycopg.errors.UndefinedColumn(
        'column "subject_class" does not exist')})
    with pytest.raises(RuntimeError, match="drugref migrate") as raised:
        cli._handle_status(conn, None)
    # ISSUE 122: the VIEW is present and one COLUMN short, so the guard must not claim
    # the relation is missing -- and Postgres's own sentence, which names the column, is
    # the whole diagnosis. `cli.main` prints only the outer message, so a `from exc` that
    # nobody renders is the same as no cause at all.
    assert 'column "subject_class" does not exist' in str(raised.value)


def test_a_dropped_view_is_not_reported_as_a_pending_migration(capsys):
    """⇒ THE CLOSED LOOP ISSUE 122 IS ABOUT, and the reason a guard must probe at all.

    `curated_target_unresolved` is ABSENT while db/035 is RECORDED APPLIED. No migration
    can bring it back -- `drugref migrate` sees the ledger, applies nothing, prints
    "migrations applied", and the operator re-runs status and reads the same sentence.
    The old guard asserted "this database predates db/035" as fact and sent them round
    that loop; there is no state of the database in which that advice works.

    THE HARM IS NOT MERELY A BAD SENTENCE. `curated_target_unresolved` reports curator
    judgement left pointing at nothing, so while it is unreadable the operator loses the
    detector AND is told the wrong thing about why.
    """
    from drugref import cli

    conn = _Conn(
        raises={"curated_target_unresolved": psycopg.errors.UndefinedTable(
            'relation "drugref.curated_target_unresolved" does not exist')},
        absent=("drugref.curated_target_unresolved",), applied=True)

    with pytest.raises(RuntimeError, match="DROPPED") as raised:
        cli._handle_status(conn, None)
    assert "Run `drugref migrate`" not in str(raised.value), (
        "the ledger says db/035 already ran: prescribing it again is the loop")


def test_a_read_that_fails_with_everything_present_refuses_to_blame_a_migration(capsys):
    """THE GUARD'S ASSUMED CAUSE, REFUTED BY ITS OWN PROBE.

    The view exists and db/035 is applied, and the read failed anyway -- a wrong
    `search_path`, a role without USAGE on schema drugref, or a base table dropped from
    under a view that still stands. Every one of those is a cause the old guard
    misattributed to a pending migration, and the operator's next move differs for each.
    """
    from drugref import cli

    conn = _Conn(
        raises={"curated_target_unresolved": psycopg.errors.UndefinedTable(
            'relation "drugref.some_base_table" does not exist')},
        absent=(), applied=True)

    with pytest.raises(RuntimeError, match="NOT a missing migration") as raised:
        cli._handle_status(conn, None)
    assert "drugref.some_base_table" in str(raised.value), (
        "the relation Postgres actually named is the one thing that resolves this, and "
        "it reached nobody: `cli.main` renders the outer message only")


def test_a_database_predating_db035_names_it_for_the_class_grain_block_too(capsys):
    """THE FIFTH BLOCK NEEDS THE GUARD ITS OWN DOCSTRING DECLINED.

    That docstring argued no guard was needed because "any database this code can
    reach at all has run migrations to at least db/029, so a missing db/035 view here
    would be a genuinely mis-shaped schema". Reaching db/029 does not imply reaching
    db/035: a database at db/029-db/034 clears both guards above and then finds none of
    this block's three views. That is not a mis-shaped schema, it is the ordinary
    upgrade path -- so the reasoning that earned blocks three and four their guards
    applies here unchanged, and fixing only the block above would move the traceback
    thirty lines down rather than removing it.
    """
    from drugref import cli

    conn = _Conn(raises={
        "gap_uncurated_class_interaction_rule": psycopg.errors.UndefinedTable(
            'relation "drugref.gap_uncurated_class_interaction_rule" does not exist')})
    with pytest.raises(RuntimeError, match="drugref migrate"):
        cli._handle_status(conn, None)


@pytest.mark.parametrize(
    "module",
    # `cli_status` joins the list the round it is created (db/035's class-grain block,
    # split out of cli.py for rule 4). It is the newest module whose whole job is
    # REPORTING on the curated overlay, which is precisely the shape that tempts an
    # embedded SELECT -- so it is covered from its first commit rather than from the
    # round somebody notices, which is how cli_curate came to be added late.
    #
    # AND `cli_interactions` DID NOT JOIN IT ON ITS FIRST COMMIT (db/038, issue 114),
    # which is the third time the paragraph above has described a discipline this list
    # then failed to follow. Its module docstring asserts the rule ("NO SQL LIVES HERE")
    # and shipped a local substitute whose first assertion could not fail -- so the
    # newest reporting module over the curated overlay was the one module exempt from
    # the guard. Added in review of PR #119; the module is clean, so it passes as-is.
    ["cli", "cli_policy", "cli_signing", "cli_signing_release", "cli_curate",
     "cli_status", "cli_interactions"])
@pytest.mark.parametrize("table", [
    "curated_interaction", "curated_condition",
    # THE FOUR SLICE-5C.4 TABLES, added the round cli_signing.py/
    # cli_signing_release.py landed -- and a defect in its own right until
    # a review round measured it: an earlier version of this test's
    # docstring NAMED these four as the reason cli_signing/cli_signing_
    # release joined `module` above, while `table` here still listed only
    # the two 5c.1 tables. That meant registering `"INSERT INTO
    # drugref.signing_key ..."` in either new file would have passed this
    # test silently -- proved by planting exactly that string plus reads of
    # assertion_signature/release_manifest and confirming all parametrized
    # cases still went green. All six curated/append-only tables these
    # four modules could reach are listed now, so the docstring's claim and
    # the parametrize list agree.
    "signing_key", "assertion_signature", "release_manifest",
    "release_manifest_entry"])
def test_the_cli_embeds_no_sql_against_a_curated_table(module, table):
    """cli.py's own discipline, pinned. `_handle_status` is allowed to embed SELECTs
    against operational views, and that exception must not creep to cover the curated
    overlay -- a Python-embedded reader of an append-only curated table is invisible to
    the pg_rewrite sweep that finds every other reader.

    MATCHED OVER PARSED STRING CONSTANTS, NOT OVER SOURCE TEXT, and the difference is
    not academic. The earlier version asserted `f"FROM drugref.{table}" not in body`
    against raw source with comment lines stripped, which was blind to a SELECT split
    across two lines, to INSERT and UPDATE, to a JOIN, and to a double space after
    FROM. The line-split hole is the one that mattered: THIS ROUND'S OWN 88-COLUMN LINT
    GATE forces long SQL to wrap, and this branch splits SQL literals in eleven places,
    so the guard was being weakened by the same commit that relies on it. `ast.parse`
    folds implicit concatenation into a single Constant, so the wrapped form and the
    one-line form are the same string here, and matching the bare table name catches
    reads and WRITES alike -- a Python-embedded writer to an append-only table being
    strictly worse than a reader.

    `cli_policy.py` is scanned too. The rule the module docstring states is ABOUT
    cli_policy ("THE POLICY COMMANDS HOLD NO SQL"), and nothing checked it.

    `cli_signing.py` AND `cli_signing_release.py` (slice 5c.4) are scanned for the
    identical reason, added the round those two files landed: `signing_key`,
    `assertion_signature`, `release_manifest` and `release_manifest_entry` are all
    curated, append-only tables a Python-embedded writer could reach invisibly. See
    the `table` parametrize list's own comment above for why all SIX tables --
    not just the two 5c.1 ones this test started with -- are checked against
    BOTH new modules: the two claims (which tables, which modules) have to be
    kept in the same place or one of them silently stops meaning anything.

    `cli_curate.py` (slice 5c.2, task 7) JOINS THE LIST FOR THE SAME REASON, not a
    new one: it is the first CLI module whose whole job is WRITING into
    `curated_interaction` (via `curation.record_interaction_judgement`/
    `curation.live_interaction_judgement`), which makes an embedded write here a
    strictly worse instance of the exact hazard this test already exists to catch.
    """
    import ast
    import importlib
    import pathlib

    source = pathlib.Path(
        importlib.import_module(f"drugref.{module}").__file__).read_text()
    constants = [node.value for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    offenders = [s for s in constants if f"drugref.{table}".lower() in s.lower()]
    assert offenders == [], (
        f"{module}.py embeds SQL naming the curated table {table}: {offenders}. "
        f"Curated reads and writes belong in curation.py, where pg_rewrite's sweep "
        f"over views can still see them.")
