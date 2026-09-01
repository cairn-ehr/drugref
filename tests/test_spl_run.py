# tests/test_spl_run.py
"""The SPL orchestrator, end to end over a corpus built from the real releases.

**THE FIXTURE CARRIES NO PROSE, AND THAT IS RULE 6 RATHER THAN CONVENIENCE.**
`tests/fixtures/spl/` holds label IDENTITY (set_id, version, effective_time,
openfda.unii) extracted from openFDA's 2026-08-22 export, and prose-free SPL
INGREDIENT SKELETONS extracted verbatim from DailyMed's 2026-08-21 Human Rx
release -- both facts, not expression. The section text is SYNTHESISED here,
naming moieties this module registers, because the owner's determination on
issue 154 is a bounded quoted window and a section committed whole to a git
repository is 100% of it. `tools/spl_make_fixture.py` is the extractor.

Everything else is real: the archives are zipped into openFDA's and DailyMed's
actual nested shapes at test time, so the readers, the `set_id` join, the
classCode nesting and the salt/moiety split are all exercised against structures
nobody wrote for a test.
"""
import dataclasses
import json
import pathlib
import uuid
import zipfile

import psycopg
import pytest

from tests.conftest import clean_scan as _scan
from drugref import spl_evidence
from drugref.ingest import (
    spl, spl_checks, spl_dailymed, spl_quote, spl_release, spl_run,
)

FIXTURE = pathlib.Path("tests/fixtures/spl")
LABELS = json.loads((FIXTURE / "openfda_labels.json").read_text())

#: Two moieties the SYNTHESISED sections name, registered by display_name so the
#: matcher resolves them for real. Fixed UUIDs so an assertion can name one.
WARFARIN = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
RIFAMPIN = uuid.UUID("00000000-0000-0000-0000-0000000000a2")

#: The synthesised wording every label carries unless a test says otherwise. Long
#: enough that the 25% budget can afford a window (the shortest real wording in
#: the corpus is 17 characters and can afford none), and it names two registry
#: moieties so a resolved label yields two pairs.
#:
#: **IT IS DELIBERATELY WRAPPED AND DOUBLE-SPACED.** Without that the raw and the
#: normalised text are the same string, and every offset assertion in this module
#: passes whichever of the two the ingest happened to index -- so the one trap
#: these offsets are most exposed to would be invisible end to end. Normalisation
#: changes the length here by hundreds of characters.
WORDING = (
    "7 DRUG   INTERACTIONS.\n\n"
    + ("Filler  sentence\n about administration. " * 20)
    + "Concomitant  warfarin increases\nthe risk of bleeding. "
    + ("More filler  about monitoring\nand dose adjustment. " * 20)
    + "Rifampin  reduces plasma\nconcentrations substantially. "
    + ("Closing filler  about clinical\n follow-up. " * 20))

#: A second wording, so the corpus has more than one and `label_count` is not
#: trivially 1 everywhere.
OTHER_WORDING = (
    "7 DRUG INTERACTIONS.\n\n"
    + ("An unrelated  statement naming\nnothing. " * 40))


def _fixture_uniis() -> set[str]:
    """Every UNII the fixture offers, from openFDA's block and DailyMed's XML."""
    uniis = {unii for label in LABELS for unii in label["uniis"]}
    for label in LABELS:
        found = spl_dailymed.extract_subject_uniis(
            (FIXTURE / "dailymed" / f"{label['set_id']}.xml").read_bytes())
        if found is not None:
            uniis.update(found.moiety_uniis)
            uniis.update(found.substance_uniis)
    return uniis


#: Display names for the fixture's own substances, registered by `_seed_registry`.
FIXTURE_NAMES = tuple(f"fixture-substance-{unii}"
                      for unii in sorted(_fixture_uniis()))

#: ⇒ **A WORDING WHERE THE QUOTE BUDGET ACTUALLY BINDS**, and the corpus needs one.
#:
#: Without it the whole end-to-end path is blind to the budget: `WORDING` names
#: two moieties over 3,700 characters, so its windows come to 256 -- comfortably
#: under 25% AND under any wrong share a bug might use. Measured: setting
#: `spl_quote.QUOTE_SHARE` to 0.95 changed NOTHING about this module's result,
#: which is db/050's "every guard in a slice passed vacuously" recurring inside
#: the round that quotes db/050 about it.
#:
#: This one names every fixture substance in a short section, so the rule has to
#: SKIP windows to stay inside the budget -- and a writer using a wrong share
#: then exceeds the budget the deferred trigger computes for itself, in SQL, from
#: `char_length`. That is what makes the determination's two homes cross-check on
#: a real ingest rather than only in a schema test.
DENSE_WORDING = "7 DRUG INTERACTIONS.\n\n" + "\n".join(
    f"Avoid  {name}\nconcomitantly." for name in FIXTURE_NAMES)


def test_the_synthesised_wordings_are_NOT_already_normalised():
    """The premise every offset assertion in this module rests on.

    If raw and normalised were the same string, an ingest indexing either would
    satisfy all of them -- and storing offsets against the raw text while
    measuring the normalised one is the one trap these offsets are most exposed
    to, because it goes wrong by a variable amount nobody can reconstruct.
    """
    for wording in (WORDING, OTHER_WORDING, DENSE_WORDING):
        assert spl.normalise_text(wording) != wording
        assert len(spl.normalise_text(wording)) < len(wording)


def _openfda_partition(path, labels, *, wording_for):
    """One openFDA partition: a single JSON document holding `results`."""
    records = [
        {"set_id": label["set_id"],
         "version": label["version"],
         "effective_time": label["effective_time"],
         "openfda": {"unii": label["uniis"],
                     "product_type": [label["product_type"]]},
         "drug_interactions": [wording_for(label)]}
        for label in labels]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("drug-label-0001-of-0001.json",
                         json.dumps({"results": records}))


def _dailymed_part(path, set_ids):
    """One DailyMed release part: a zip of zips, each holding one label's XML.

    The nesting is DailyMed's own, and it is the reason `iter_release_labels`
    exists -- each outer member is itself a zip holding the XML plus the label's
    images.
    """
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
        for set_id in set_ids:
            skeleton = (FIXTURE / "dailymed" / f"{set_id}.xml").read_bytes()
            inner_path = path.parent / f"{set_id}.inner.zip"
            with zipfile.ZipFile(inner_path, "w") as inner:
                inner.writestr(f"{set_id}.xml", skeleton)
                # A JPEG member, because a real one carries the label's images
                # and the reader must take only the .xml.
                inner.writestr(f"{set_id}-01.jpg", b"\xff\xd8\xff\xe0not-an-image")
            outer.write(inner_path, f"{set_id}.zip")
            inner_path.unlink()


def _wording_for(label) -> str:
    """Which of the three wordings a label carries.

    THREE, not one, and each earns its place: `WORDING` names two moieties over a
    long section (the ordinary case), `OTHER_WORDING` names none (the wording
    that keeps its identity and stores no prose), and `DENSE_WORDING` names many
    over a short one (the only one where the quote budget BINDS).
    """
    if label is LABELS[-1]:
        return OTHER_WORDING
    if label is LABELS[-2]:
        return DENSE_WORDING
    return WORDING


@pytest.fixture
def corpus(tmp_path):
    """openFDA and DailyMed archives in their real shapes. Returns both paths."""
    openfda_dir = tmp_path / "OPENFDA"
    openfda_dir.mkdir()
    _openfda_partition(
        openfda_dir / "drug-label-0001-of-0001.json.zip", LABELS,
        wording_for=_wording_for)
    part = tmp_path / "dm_spl_release_human_rx_part1.zip"
    _dailymed_part(part, [label["set_id"] for label in LABELS])
    return openfda_dir, [part]


def _seed_registry(conn, *, uniis=None):
    """A small REAL registry: two named moieties, and the fixture's own UNIIs.

    `uniis` narrows which of the fixture's UNIIs drugref holds. It is how the two
    routes with no natural example in this corpus get exercised for real:
    withholding a label's active-MOIETY UNII while keeping its SALT reaches
    `dailymed_active_substance`, and withholding both reaches `unresolved`. A
    registry is allowed to be incomplete -- that is what issue 67 and the 200
    labels carrying an unheld UNII are -- so narrowing it is not a contrivance.

    conftest's `_migrated` fixture applies SCHEMA ONLY, so without this every
    label is honestly unresolved, every route bucket but the two negative ones is
    zero, and the pair path is never taken at all -- which is the shape of the
    vacuous green db/050's review round found.

    THE UNIIs ARE THE FIXTURE'S OWN, read out of the extracted labels rather than
    invented, so `openfda_unii` resolves for exactly the labels openFDA keys and
    `dailymed_active_moiety` for the ones it does not.
    """
    seed_run = conn.execute(
        "INSERT INTO drugref.ingest_run "
        "(source, upstream_release, source_checksum, writer) "
        "VALUES ('UNII', 'test', 'test', 'unii_run') RETURNING ingest_run_id"
    ).fetchone()[0]
    for moiety_uuid, name in ((WARFARIN, "warfarin"), (RIFAMPIN, "rifampin")):
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
            (moiety_uuid, name, seed_run))

    # Every UNII the fixture's labels or skeletons offer, each registered as its
    # own moiety -- which is exactly what drugref does with a salt, and is why
    # blending the salt into the subject would double a salt product's pairs.
    for index, unii in enumerate(sorted(
            _fixture_uniis() if uniis is None else uniis)):
        moiety_uuid = uuid.UUID(int=0xB000 + index)
        conn.execute(
            "INSERT INTO drugref.substance_moiety "
            "(moiety_uuid, display_name, first_seen_ingest) VALUES (%s, %s, %s)",
            (moiety_uuid, f"fixture-substance-{unii}", seed_run))
        conn.execute(
            "INSERT INTO drugref.identity_claim "
            "(moiety_uuid, scheme, value, ingest_run) VALUES (%s, 'UNII', %s, %s)",
            (moiety_uuid, unii, seed_run))
    conn.commit()
    return seed_run


@pytest.fixture
def _clean(conn):
    """`ingest_spl` COMMITS, so the conn fixture's rollback cannot undo it.

    substance_moiety and identity_claim are listed EXPLICITLY rather than left to
    the CASCADE, matching test_drugcentral_run's fixture: this module commits
    real registry rows so the resolution path fires for real, and they must not
    survive into the next test file any more than the SPL rows do.
    """
    yield
    conn.execute(
        "TRUNCATE drugref.spl_wording_quote, drugref.spl_entity_occurrence, "
        "drugref.spl_label_subject, drugref.spl_label, drugref.spl_wording, "
        "drugref.open_question, drugref.identity_claim, "
        "drugref.substance_moiety, drugref.ingest_run CASCADE")
    conn.commit()


def _ingest(conn, corpus, **overrides):
    openfda_dir, parts = corpus
    kwargs = dict(openfda_dir=openfda_dir, dailymed_parts=parts,
                  release="fixture-openfda+dailymed")
    return spl_run.ingest_spl(conn, **(kwargs | overrides))


# --------------------------------------------------------------------------
# The whole path
# --------------------------------------------------------------------------

@pytest.mark.usefixtures("_clean")
def test_the_expensive_pass_runs_with_NO_SNAPSHOT_HELD(conn, corpus, monkeypatch):
    """⇒ PINNED BY ITS CAUSE, because the cost is invisible to a fixture.

    `load_registry` is the first statement on a non-autocommit connection, so it
    opens a transaction; `open_run` is on the far side of the DailyMed scan and
    the 19.3 GB checksum. Measured on the real releases that gap is ~50 s -- it
    was read as ~12.5 minutes for one round, which was the WHOLE ingest's figure
    describing a part of it. During that gap a production node would show this
    backend as `idle in transaction`, pinning `xmin` database-wide so autovacuum
    reclaims nothing in any table, and offering itself to
    `idle_in_transaction_session_timeout` at the far end of the most expensive
    step in the ingest.

    A fixture corpus of three wordings scans in milliseconds, so NO end-to-end
    assertion can see this: the defect is duration, not result. What is
    observable is the CAUSE -- whether a snapshot is held when the expensive pass
    starts -- so that is what this asserts, the same reasoning `analyze_source
    _tables` is pinned by `pg_class.reltuples` rather than by a stopwatch.
    """
    seen = []
    real_scan = spl_release.scan_release

    def watching_scan(*args, **kwargs):
        seen.append(conn.info.transaction_status)
        return real_scan(*args, **kwargs)

    _seed_registry(conn)
    monkeypatch.setattr(spl_run.spl_release, "scan_release", watching_scan)
    _ingest(conn, corpus)

    assert seen == [psycopg.pq.TransactionStatus.IDLE], (
        "the DailyMed scan ran inside an open transaction: the registry read "
        "before it must be rolled back, or the snapshot is held for the whole "
        "scan and checksum")


@pytest.mark.usefixtures("_clean")
def test_the_fixture_corpus_ingests_and_reconciles(conn, corpus):
    _seed_registry(conn)
    summary = _ingest(conn, corpus)

    assert summary.labels == len(LABELS)
    assert summary.records_read == len(LABELS)
    # Two synthesised wordings over eight labels: the de-duplication factor is
    # real here, not 1:1, which is what makes label_count mean anything.
    assert summary.wordings == 3
    assert summary.pairs > 0
    assert summary.occurrences > 0


@pytest.mark.usefixtures("_clean")
def test_every_route_the_corpus_can_reach_is_taken(conn, corpus):
    """The fixture was chosen half-keyed and half-unkeyed for exactly this.

    A corpus exercising one route of five would leave the other four's code
    unrun while every count still reconciled.
    """
    _seed_registry(conn)
    summary = _ingest(conn, corpus)
    assert summary.labels_by_route["openfda_unii"] == 4
    assert summary.labels_by_route.get("dailymed_active_moiety", 0) == 4
    assert summary.resolved_labels == 8


#: The one fixture label whose DailyMed reading splits salt from moiety:
#: `<ingredientSubstance>` carries the salt UNIIs and the nested `<activeMoiety>`
#: the base ones. openFDA's own block on it carries the SALT, which is why it
#: needs blanking before the DailyMed routes can be reached at all.
SALT_LABEL = "038cf2ba-ad08-4981-a3cc-bff0e4ba5dfb"
SALT_UNIIS = ("4S9CL2DY2H", "P8Y54F701R")
SALT_MOIETY_UNIIS = ("5JKY92S7BR", "E6GNX3HHTE")


def _corpus_with_blanked_openfda(tmp_path, set_id):
    """The same corpus with ONE label's `openfda` block emptied.

    Representative rather than contrived: openFDA leaves that block present and
    EMPTY on 59.6% of section-carrying labels, which is the entire reason the
    DailyMed pass exists. The DailyMed side of this label stays exactly as
    extracted.
    """
    labels = [dict(label, uniis=[]) if label["set_id"] == set_id else label
              for label in LABELS]
    openfda_dir = tmp_path / "OPENFDA-blanked"
    openfda_dir.mkdir()
    _openfda_partition(openfda_dir / "drug-label-0001-of-0001.json.zip", labels,
                       wording_for=lambda label: WORDING)
    part = tmp_path / "dm_blanked.zip"
    _dailymed_part(part, [label["set_id"] for label in labels])
    return openfda_dir, [part]


@pytest.mark.usefixtures("_clean")
def test_the_SALT_route_fires_when_drugref_holds_only_the_salt(conn, tmp_path):
    """16 labels take this route on the real release, and it is COUNTED APART.

    It needs the salt-to-base step drugref does not have (issue 67), so folding
    it into `dailymed_active_moiety` would credit recovery drugref cannot
    actually perform.
    """
    _seed_registry(conn, uniis=set(SALT_UNIIS))
    summary = _ingest(conn, _corpus_with_blanked_openfda(tmp_path, SALT_LABEL))
    assert summary.labels_by_route["dailymed_active_substance"] == 1


@pytest.mark.usefixtures("_clean")
def test_the_MOIETY_wins_over_the_salt_when_drugref_holds_both(conn, tmp_path):
    """And the salt is NOT a second subject beside it -- the defect that
    published 31,618 pairs where the exclusive rule gives 29,258."""
    _seed_registry(conn, uniis=set(SALT_UNIIS) | set(SALT_MOIETY_UNIIS))
    summary = _ingest(conn, _corpus_with_blanked_openfda(tmp_path, SALT_LABEL))
    assert summary.labels_by_route.get("dailymed_active_substance", 0) == 0
    assert summary.labels_by_route["dailymed_active_moiety"] == 1
    subjects = conn.execute(
        "SELECT count(*) FROM drugref.spl_label_subject WHERE set_id = %s",
        (SALT_LABEL,)).fetchone()[0]
    # TWO moieties, because it is a combination product -- not FOUR, which is
    # what blending the salts in would give.
    assert subjects == 2


@pytest.mark.usefixtures("_clean")
def test_a_label_READ_from_dailymed_and_still_unkeyable_is_unresolved(
        conn, tmp_path):
    """Distinct from `absent_from_dailymed`: this is REGISTRY-COVERAGE work.

    200 labels on the real release carry a UNII no live identity_claim holds, and
    folding them into the absent bucket would report a gap in drugref as a gap in
    the release.
    """
    _seed_registry(conn, uniis={"4S9CL2DY2H"})
    summary = _ingest(conn, _corpus_with_blanked_openfda(tmp_path, SALT_LABEL))
    assert summary.labels_by_route["unresolved"] >= 4
    routes = {row[0] for row in conn.execute(
        "SELECT DISTINCT route FROM drugref.gap_unresolved_spl_subject")}
    assert routes == {"unresolved"}


@pytest.mark.usefixtures("_clean")
def test_a_label_absent_from_the_dailymed_parts_says_so(conn, corpus, tmp_path):
    """'Not published there' and 'published and unkeyable' are different findings."""
    _seed_registry(conn)
    openfda_dir, _parts = corpus
    empty = tmp_path / "dm_spl_release_human_rx_part9.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    summary = _ingest(conn, (openfda_dir, [empty]))
    assert summary.labels_by_route["absent_from_dailymed"] == 4
    assert summary.labels_by_route["openfda_unii"] == 4


@pytest.mark.usefixtures("_clean")
def test_the_wording_register_carries_the_de_duplication_factor(conn, corpus):
    _seed_registry(conn)
    _ingest(conn, corpus)
    counts = dict(conn.execute(
        "SELECT text_key, label_count FROM drugref.spl_wording").fetchall())
    assert sorted(counts.values()) == [1, 1, 6]
    lengths = dict(conn.execute(
        "SELECT text_key, char_length FROM drugref.spl_wording").fetchall())
    assert lengths[spl.section_key(WORDING)] == len(spl.normalise_text(WORDING))


@pytest.mark.usefixtures("_clean")
def test_occurrences_cut_the_matched_name_back_out_of_the_wording(conn, corpus):
    """The offsets and the wording have to describe ONE string.

    Storing offsets against the raw text while measuring the normalised one is
    the silent way to hand a reader the wrong words, and it goes wrong by a
    variable amount nobody can reconstruct after the fact.
    """
    _seed_registry(conn)
    _ingest(conn, corpus)
    normalised = spl.normalise_text(WORDING)
    rows = conn.execute(
        "SELECT o.char_start, o.char_end, m.display_name "
        "  FROM drugref.spl_entity_occurrence o "
        "  JOIN drugref.substance_moiety m USING (moiety_uuid) "
        " WHERE o.text_key = %s", (spl.section_key(WORDING),)).fetchall()
    assert rows
    for char_start, char_end, display_name in rows:
        assert normalised[char_start:char_end].lower() == display_name.lower()


@pytest.mark.usefixtures("_clean")
def test_a_stored_quote_is_exactly_the_characters_its_offsets_name(conn, corpus):
    _seed_registry(conn)
    _ingest(conn, corpus)
    normalised = spl.normalise_text(WORDING)
    rows = conn.execute(
        "SELECT char_start, char_end, quote_text FROM drugref.spl_wording_quote "
        " WHERE text_key = %s ORDER BY ordinal",
        (spl.section_key(WORDING),)).fetchall()
    assert rows
    for char_start, char_end, quote_text in rows:
        assert quote_text == normalised[char_start:char_end]


@pytest.mark.usefixtures("_clean")
def test_the_stored_prose_stays_inside_the_budget_on_a_real_run(conn, corpus):
    """The determination, asserted against what actually landed.

    Not against what the writer intended: the deferred trigger has already
    passed at commit, and this reads the same quantity back out independently.
    """
    _seed_registry(conn)
    _ingest(conn, corpus)
    rows = conn.execute(
        "SELECT w.char_length, "
        "       coalesce(sum(q.char_end - q.char_start), 0) AS stored "
        "  FROM drugref.spl_wording w "
        "  LEFT JOIN drugref.spl_wording_quote q "
        "    ON q.ingest_run = w.ingest_run AND q.source = w.source "
        "   AND q.text_key = w.text_key "
        " GROUP BY w.ingest_run, w.source, w.text_key, w.char_length").fetchall()
    assert rows
    for char_length, stored in rows:
        assert stored <= spl_quote.quote_budget(char_length)


@pytest.mark.usefixtures("_clean")
def test_the_budget_BINDS_on_the_dense_wording(conn, corpus):
    """⇒ THE TEST THAT KILLS A WRONG SHARE, and the corpus was changed to have it.

    Measured before it existed: setting `spl_quote.QUOTE_SHARE` to 0.95 left
    every test in this module passing, because no wording's windows came near
    any budget. Here the rule has to SKIP windows, so the number stored is a
    function of the share -- and a writer using the wrong one exceeds the budget
    db/051's trigger computes for itself in SQL and is refused at commit.
    """
    _seed_registry(conn)
    _ingest(conn, corpus)
    key = spl.section_key(DENSE_WORDING)
    (char_length, stored, windows) = conn.execute(
        "SELECT w.char_length, "
        "       coalesce(sum(q.char_end - q.char_start), 0), count(q.*) "
        "  FROM drugref.spl_wording w "
        "  LEFT JOIN drugref.spl_wording_quote q "
        "    ON q.ingest_run = w.ingest_run AND q.source = w.source "
        "   AND q.text_key = w.text_key "
        " WHERE w.text_key = %s "
        " GROUP BY w.char_length", (key,)).fetchone()
    (named,) = conn.execute(
        "SELECT count(DISTINCT moiety_uuid) FROM drugref.spl_entity_occurrence "
        " WHERE text_key = %s", (key,)).fetchone()
    assert named >= 8, "the dense wording must name enough moieties to bind"
    assert stored <= spl_quote.quote_budget(char_length)
    # THE BINDING ITSELF: fewer windows than distinct moieties named, which is
    # only true because the budget refused some.
    assert 0 < windows < named


@pytest.mark.usefixtures("_clean")
def test_the_wording_naming_nothing_stores_no_prose_at_all(conn, corpus):
    """28.4% of named moieties lose their window; a wording naming NOTHING loses
    every window, and keeps its identity and its label."""
    _seed_registry(conn)
    _ingest(conn, corpus)
    (quotes,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_wording_quote WHERE text_key = %s",
        (spl.section_key(OTHER_WORDING),)).fetchone()
    (wordings,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_wording WHERE text_key = %s",
        (spl.section_key(OTHER_WORDING),)).fetchone()
    assert quotes == 0
    assert wordings == 1


# --------------------------------------------------------------------------
# The read path
# --------------------------------------------------------------------------

@pytest.mark.usefixtures("_clean")
def test_the_pair_view_is_orientation_normalised_and_one_row_per_pair(conn, corpus):
    _seed_registry(conn)
    summary = _ingest(conn, corpus)
    rows = conn.execute(
        "SELECT moiety_lo, moiety_hi FROM drugref.spl_ddi_pair").fetchall()
    assert len(rows) == summary.pairs
    assert len(set(rows)) == summary.pairs
    for lo, hi in rows:
        assert lo < hi


@pytest.mark.usefixtures("_clean")
def test_the_evidence_view_carries_the_citation_and_the_quote(conn, corpus):
    _seed_registry(conn)
    _ingest(conn, corpus)
    rows = conn.execute(
        "SELECT set_id, version, effective_time, text_key, quote_text "
        "  FROM drugref.spl_ddi_evidence LIMIT 5").fetchall()
    assert rows
    known = {(label["set_id"], label["version"]) for label in LABELS}
    for set_id, version, effective_time, text_key, quote_text in rows:
        assert (set_id, version) in known
        assert effective_time
        assert text_key
        assert quote_text is None or quote_text in spl.normalise_text(WORDING)


@pytest.mark.usefixtures("_clean")
def test_an_unresolved_subject_is_ABSENT_from_the_pairs_and_PRESENT_in_the_gap(
        conn, corpus, tmp_path):
    _seed_registry(conn)
    openfda_dir, _parts = corpus
    empty = tmp_path / "dm_spl_release_human_rx_part9.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    _ingest(conn, (openfda_dir, [empty]))
    gap = conn.execute(
        "SELECT set_id, route FROM drugref.gap_unresolved_spl_subject").fetchall()
    assert len(gap) == 4
    assert {route for _set_id, route in gap} == {"absent_from_dailymed"}
    evidence = {row[0] for row in conn.execute(
        "SELECT set_id FROM drugref.spl_ddi_evidence").fetchall()}
    assert not ({set_id for set_id, _ in gap} & evidence)


@pytest.mark.usefixtures("_clean")
def test_the_gap_view_is_NOT_registered_as_an_open_question_kind(conn, corpus):
    """Every other gap_* view here feeds questions._GAP_SOURCES; this one must
    not. A curator cannot answer 'not in the current DailyMed release', and
    34,542 immortal question_uuids would bury the eighteen kinds they can."""
    from drugref import questions
    views = {spec["view"] for spec in questions._GAP_SOURCES.values()}
    assert "gap_unresolved_spl_subject" not in views
    _seed_registry(conn)
    _ingest(conn, corpus)
    (questions_written,) = conn.execute(
        "SELECT count(*) FROM drugref.open_question").fetchone()
    assert questions_written == 0


@pytest.mark.usefixtures("_clean")
def test_exact_ddi_pair_is_UNCHANGED_by_this_slice(conn, corpus):
    """SPL evidence means 'a label names both drugs', not 'an authority asserts
    they interact'. A read path that could not tell them apart would make the
    stronger claim unfalsifiable."""
    _seed_registry(conn)
    summary = _ingest(conn, corpus)
    (exact,) = conn.execute(
        "SELECT count(*) FROM drugref.exact_ddi_pair").fetchone()
    assert summary.pairs > 0
    assert exact == 0


# --------------------------------------------------------------------------
# Rebuild safety
# --------------------------------------------------------------------------

@pytest.mark.usefixtures("_clean")
def test_the_projection_is_ANALYZED_before_its_own_read_backs(conn, corpus):
    """⇒ WITHOUT THIS THE INGEST DOES NOT FINISH ON THE REAL CORPUS.

    Every read-back in the orchestrator queries a table the same transaction just
    bulk-loaded, so the planner costs them as if those tables were empty and
    picks a nested loop over 1.3 million occurrence rows. Measured on the real
    releases: the self-pair count ran 25 minutes at 100% CPU and had not
    finished.

    A performance property cannot be asserted as a timing on a fixture this
    small, so what is pinned is the CAUSE: after an ingest, PostgreSQL must have
    real row estimates for every table this source owns. `reltuples` is -1 on a
    table that has never been analyzed, which is exactly the state that produced
    the stall.
    """
    _seed_registry(conn)
    _ingest(conn, corpus)
    estimates = dict(conn.execute(
        "SELECT relname, reltuples FROM pg_class "
        " WHERE relnamespace = 'drugref'::regnamespace "
        "   AND relname = ANY(%s)", (list(spl_evidence.SPL_TABLES),)).fetchall())
    assert set(estimates) == set(spl_evidence.SPL_TABLES)
    for table, reltuples in estimates.items():
        assert reltuples >= 0, f"{table} was never analysed (reltuples={reltuples})"


def _reltuples(conn, table):
    """PostgreSQL's row estimate for one drugref table, -1 if never analysed."""
    (estimate,) = conn.execute(
        "SELECT reltuples FROM pg_class "
        " WHERE relnamespace = 'drugref'::regnamespace AND relname = %s",
        (table,)).fetchone()
    return estimate


def _in_source_fk_edges(conn):
    """`{child table: (parent table, ...)}` for the FKs INSIDE this source.

    Restricted to parents in `SPL_TABLES`, because those are the ones this one
    transaction bulk-loads and therefore the ones that can be unanalyzed when the
    check fires. A parent outside the source -- `ingest_run`, `substance_moiety`
    -- was loaded by some earlier committed run and already carries statistics.

    Read from `pg_constraint` rather than restated, so a foreign key added to
    `db/051`'s successor is covered without anyone remembering to come here.
    """
    edges: dict[str, list[str]] = {}
    for child, parent in conn.execute(
            "SELECT child.relname, parent.relname "
            "  FROM pg_constraint c "
            "  JOIN pg_class child  ON child.oid  = c.conrelid "
            "  JOIN pg_class parent ON parent.oid = c.confrelid "
            " WHERE c.contype = 'f' "
            "   AND c.connamespace = 'drugref'::regnamespace").fetchall():
        if child in spl_evidence.SPL_TABLES and parent in spl_evidence.SPL_TABLES:
            edges.setdefault(child, []).append(parent)
    return {child: tuple(sorted(parents)) for child, parents in edges.items()}


@pytest.mark.usefixtures("_clean")
def test_a_FK_PARENT_is_ANALYZED_BEFORE_THE_CHILD_THAT_REFERENCES_IT_is_loaded(
        conn, corpus, monkeypatch):
    """⇒ ISSUE 160, AND THE ANALYZE ONE TEST UP IS TOO LATE TO PREVENT IT.

    Measured on the real releases (2026-09-01, `drugref_spl160`): the `COPY` of
    73,867 rows into `spl_label_subject` ran **630 s at 96% of one core**, while
    the 1,436,131 rows of `spl_entity_occurrence` + `spl_wording_quote` -- 19.4x
    more rows, same transaction, same client -- took **35 s**. Row volume is
    therefore not the cause, and neither is `COPY`.

    THE CAUSE, taken from a stack sample of the backend rather than guessed: 100%
    of samples sat in `RI_FKey_check_ins`, the foreign-key check fired as an
    after-row trigger. Its query is `... WHERE ingest_run = $1 AND source = $2
    AND set_id = $3 AND version = $4 FOR KEY SHARE`, and the planner may satisfy
    that with ANY index on the parent whose LEADING RUN of key columns lies among
    those four -- the same criterion `_fk_exposure` below censuses.
    `spl_label` carries two: `spl_label_pkey` on all four, and
    `spl_label_by_wording` on `(ingest_run, source, text_key)`. With the parent
    freshly `COPY`d and never analysed -- `relpages = 0`, `reltuples = -1` --
    BOTH plans cost an identical 8.44, and the tie was broken towards
    `spl_label_by_wording`, whose index condition matches ALL 68,550 rows and
    discards 68,549 of them in a filter. One `ANALYZE` of the parent moves it to
    the primary key.

    ⇒ AND THAT IS WHY THE ISSUE'S OWN REFUTATION OF THE FOREIGN KEY WAS WRONG.
    `analyze_source_tables`'s docstring said RI triggers "use a plan pinned to
    the parent's primary key rather than a re-planned query". The plan IS pinned
    -- to whatever was chosen at FIRST USE, which is inside the load, before any
    `ANALYZE` has run. Pinned is not the same as pinned to the primary key.

    A duration cannot be asserted on a fixture this small, so what is pinned is
    the CAUSE: at the moment a child is written, its foreign-key parent must
    already carry statistics DESCRIBING ITS ROWS.

    ⇒ **AND THE PREDICATE IS `> 0`, NOT `>= 0`, WHICH IS NOT A STYLE CHOICE.**
    `reltuples` is -1 on a table that was never analysed and **0.0 on one that
    was analysed WHILE STILL EMPTY** -- and 0.0 is not a lesser version of the
    bug, it IS the bug: an empty parent has `relpages = 0` exactly as an
    unanalyzed one does, so it pins the same catastrophic plan. Measured with
    `plan_cache_mode = force_generic_plan` on a replica of `spl_label`'s shape:
    analysed-while-empty then loaded to 68,550 rows plans onto
    `spl_label_by_wording` with a filter; analysed AFTER loading plans onto
    `spl_label_pkey`. `>= 0` let two mutants live -- moving an `ANALYZE` to
    before its own table's write, and replacing both calls with a single
    `analyze_source_tables` right after `clear_source_spl`, which is the tidy-up
    a future reader is most likely to attempt.

    ⇒ **THE EDGES ARE DERIVED FROM THE CATALOG, NOT LISTED HERE.** An earlier
    version of this test named three writers by hand and claimed to cover "every"
    foreign key; there are four, and one of the three watches was inert because
    two of them keyed on the same parent. Hooking `_copy` -- the single chokepoint
    every writer goes through -- and reading the edges out of `pg_constraint`
    means a child table added to the orchestrator is covered the day it is added,
    which is the one thing a hand-written list cannot promise.
    """
    edges = _in_source_fk_edges(conn)
    observed: dict[tuple[str, str], float] = {}
    real_copy = spl_evidence._copy

    def watching_copy(*args, **kwargs):
        # `_copy(conn, row_type, rows, ...)`: the child table is the row type's,
        # via the writer's own one home for that mapping.
        child = spl_evidence._TABLE_FOR_ROW[args[1].__name__]
        for parent in edges.get(child, ()):
            # FIRST use only -- the RI plan is chosen when the first child row
            # of this load is checked, so a later chunk's reading is too late to
            # be the thing under test.
            observed.setdefault((child, parent), _reltuples(conn, parent))
        return real_copy(*args, **kwargs)

    monkeypatch.setattr(spl_evidence, "_copy", watching_copy)

    _seed_registry(conn)
    _ingest(conn, corpus)

    # THE GATE: every in-source foreign key whose parent this transaction also
    # bulk-loads. A new child table adds an entry here and forces somebody to
    # decide -- and unlike the census below it cannot be closed by editing this
    # literal, because the assertion underneath is derived from what actually ran.
    assert set(observed) == {
        ("spl_label", "spl_wording"),
        ("spl_label_subject", "spl_label"),
        ("spl_entity_occurrence", "spl_wording"),
        ("spl_wording_quote", "spl_wording"),
    }
    for (child, parent), reltuples in sorted(observed.items()):
        assert reltuples > 0, (
            f"{parent} carried no row statistics when {child}, which references "
            f"it, was loaded (reltuples={reltuples}; -1 is never analysed, 0 is "
            "analysed while still empty, and both pin the same wrong "
            "foreign-key check plan). That plan is chosen at this moment, so an "
            "ANALYZE later in the run cannot buy back what this COPY pays.")


def _fk_exposure(conn):
    """Every (foreign key, parent index) pair the planner can use but that does
    NOT pin the check to a single row.

    An FK check reads `WHERE p1 = $1 AND ... AND pn = $n`, so the planner can use
    any parent index whose LEADING columns are among p1..pn. If that leading run
    is shorter than the whole key, the index condition matches more than one row
    and the remainder are discarded by a filter.

    **WRITTEN IN PYTHON FOR THE `indnkeyatts` SLICE**, which is the one step that
    can produce a FALSE NEGATIVE -- the direction that matters for a gate.
    `indkey` runs over key columns AND `INCLUDE` columns; an INCLUDE column that
    happens to be one of p1..pn would extend `leading` to the full key length and
    silently declare a genuinely exposed index safe. INCLUDE columns cannot serve
    an index condition, so the slice to `indnkeyatts` is load-bearing rather than
    tidy. (An earlier version of this docstring blamed a zero-based `int2vector`
    off-by-one instead. Nothing here subscripts the array -- it iterates and
    compares values -- so that hazard does not exist and the real one was
    unnamed.)

    **IT OVER-REPORTS, DELIBERATELY.** `indisunique`, `indpred` and `indisvalid`
    are not consulted, so a unique index on a shorter prefix, or a partial index
    the RI query can never match, is flagged even though it pins one row. A gate
    that fails loudly and makes somebody look is the right error to make; the
    reverse would hand back a 630-second `COPY`. Expression-leading indexes
    (`indkey` entry 0) and single-column keys are correctly never flagged.

    ⇒ **ONE DIRECTION ONLY: the INSERT side.** This censuses parent indexes, which
    is what `RI_FKey_check_ins` uses. The DELETE side (`RI_FKey_noaction_del`,
    which `clear_source_spl` fires) scans the CHILD and is not covered here.
    """
    exposed = []
    constraints = conn.execute(
        "SELECT conname, conrelid::regclass::text, confrelid, "
        "       confrelid::regclass::text, confkey "
        "  FROM pg_constraint "
        " WHERE contype = 'f' AND connamespace = 'drugref'::regnamespace").fetchall()
    for _conname, child, parent_oid, parent, parent_cols in constraints:
        indexes = conn.execute(
            # indkey is an int2vector; psycopg hands that back as a STRING
            # unless it is cast, and the census then silently finds nothing.
            "SELECT indexrelid::regclass::text, indkey::int2[], indnkeyatts "
            "  FROM pg_index WHERE indrelid = %s", (parent_oid,)).fetchall()
        for index, indkey, key_columns in indexes:
            leading = 0
            for column in list(indkey)[:key_columns]:
                if column not in parent_cols:
                    break
                leading += 1
            if 0 < leading < len(parent_cols):
                exposed.append((child, parent, index))
    return sorted(exposed)


def test_ONE_foreign_key_in_the_schema_can_be_planned_onto_a_LOOSE_index(conn):
    """The census behind the test above, so a SECOND exposure cannot arrive quietly.

    Measured over all 138 foreign keys schema `drugref` held on 2026-09-01:
    exactly ONE is exposed, and it is issue 160's. **What this test guards is the
    exposure LIST, not that count** -- the number is a dated measurement, and a
    migration that adds a foreign key moves it without weakening anything.

    This is a GATE, not a defect report -- the loose index is wanted, and the
    mitigation is that the parent is analysed before the child is loaded. A new
    entry here means another orchestrator has to make that same guarantee, and
    the point of the assertion is to force somebody to decide rather than to
    inherit a 630-second `COPY`.

    Note the exposure is a property of a PARENT'S INDEXES, so the edit that
    creates one is `CREATE INDEX` in a migration -- nowhere near an orchestrator,
    and by somebody with no reason to be thinking about foreign-key check plans.
    """
    assert _fk_exposure(conn) == [
        ("drugref.spl_label_subject", "drugref.spl_label",
         "drugref.spl_label_by_wording")]


@pytest.mark.usefixtures("_clean")
def test_a_re_ingest_REPLACES_rather_than_accumulates(conn, corpus):
    """The per-source rebuild, which is what makes 'rebuildable projection' true.

    Measured on every count, not just the pairs: a projection that grew a little
    on each ingest is the defect issue 43 exists to prevent, and it is invisible
    in any single number.
    """
    _seed_registry(conn)
    first = _ingest(conn, corpus)
    second = _ingest(conn, corpus)
    assert second == first
    for table in spl_evidence.SPL_TABLES:
        (rows,) = conn.execute(f"SELECT count(*) FROM drugref.{table}").fetchone()
        (runs,) = conn.execute(
            f"SELECT count(DISTINCT ingest_run) FROM drugref.{table}").fetchone()
        assert runs == 1, table
        assert rows > 0, table


# --------------------------------------------------------------------------
# The refusals -- each SHOWN firing
# --------------------------------------------------------------------------

def test_ANALYZING_a_table_this_source_does_not_own_is_refused():
    """`ANALYZE` interpolates its identifier, so the name must be a constant.

    The same rule `_copy` and `db.clear_source_tables` already state, shown
    firing rather than asserted in a comment: a table name reaching this from
    anywhere but `SPL_TABLES` is refused before it is spliced into SQL.

    NO `conn` FIXTURE, and that is the assertion: the refusal happens before any
    SQL is built, so a connection is not needed to show it. Taking one would gate
    this on `DRUGREF_TEST_DSN` and skip the guard on exactly the machines least
    likely to have a database -- someone else's first checkout.
    """
    with pytest.raises(ValueError, match="not among this source's tables"):
        spl_evidence.analyze_loaded_table(None, "substance_moiety")


def test_ANALYZING_an_EMPTY_list_of_tables_is_refused():
    """`ANALYZE` with no table list means EVERY table in the database.

    Unreachable from either call site today -- one passes a 1-tuple, the other
    `SPL_TABLES` -- so it is refused rather than handled, on `_copy`'s stated
    grounds for its own unreachable refusal: the day it becomes reachable is the
    day the ingest takes a lock on every table in the database until COMMIT.
    """
    with pytest.raises(ValueError, match="no tables"):
        spl_evidence._analyze(None, ())


@pytest.mark.usefixtures("_clean")
def test_an_ingest_against_an_EMPTY_registry_is_refused(conn, corpus):
    """Not merely reported: it would publish nothing while clearing everything.

    The message names the two feeds that have to run first, because 'resolved 0
    of 68,550' is a symptom and 'run unii and chebi' is the cause.
    """
    with pytest.raises(ValueError, match="run `ingest unii`"):
        _ingest(conn, corpus)


@pytest.mark.usefixtures("_clean")
def test_a_corpus_carrying_no_sections_is_refused_before_anything_is_cleared(
        conn, corpus, tmp_path):
    _seed_registry(conn)
    _ingest(conn, corpus)
    (before,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_label").fetchone()

    empty_dir = tmp_path / "EMPTY"
    empty_dir.mkdir()
    with zipfile.ZipFile(
            empty_dir / "drug-label-0001-of-0001.json.zip", "w") as archive:
        archive.writestr("drug-label-0001-of-0001.json",
                         json.dumps({"results": [{"set_id": "x"}]}))
    with pytest.raises(ValueError, match="no label carries section"):
        _ingest(conn, (empty_dir, corpus[1]))
    (after,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_label").fetchone()
    assert after == before > 0


@pytest.mark.usefixtures("_clean")
def test_a_missing_openfda_directory_is_refused_by_name(conn, tmp_path):
    with pytest.raises(ValueError, match="no openFDA partitions"):
        spl_run.ingest_spl(conn, openfda_dir=tmp_path, dailymed_parts=[],
                           release="x")


@pytest.mark.usefixtures("_clean")
def test_an_autocommit_connection_is_refused(conn, corpus):
    """It would void the rollback AND the quote budget: the trigger is deferred
    to commit, and under autocommit every row commits alone, so it would fire
    against a wording holding one window and pass every time."""
    conn.commit()
    conn.autocommit = True
    try:
        with pytest.raises(ValueError, match="autocommit"):
            _ingest(conn, corpus)
    finally:
        conn.autocommit = False


@pytest.mark.usefixtures("_clean")
@pytest.mark.parametrize("chunk", [1, 2])
def test_the_corpus_is_written_IDENTICALLY_when_it_spans_several_chunks(
        conn, corpus, monkeypatch, chunk):
    """⇒ `WORDING_CHUNK` SAID A TEST DID THIS, AND NO TEST MENTIONED IT.

    `range(0, len(keys), WORDING_CHUNK)` -> `range(0, len(keys), WORDING_CHUNK +
    1)` -- the classic stride-wider-than-the-slice bug, which silently drops one
    wording per chunk -- left the whole suite green. The fixture corpus is three
    wordings against a chunk of 2,000, so the loop was only ever entered once and
    the chunk boundary was never crossed.

    Chunking is an internal batching choice, so the assertion is that it changes
    NOTHING: the same corpus written a wording or two at a time must produce
    exactly the counts it produces in a single chunk.

    BOTH chunk sizes are needed, and that is not belt-and-braces. The stride bug
    drops `keys[chunk]` -- a different wording for each size -- and one of the
    three fixture wordings (`OTHER_WORDING`) names no moiety, so dropping THAT
    one moves no counter. Parametrising guarantees at least one size drops a
    wording that actually contributes.
    """
    _seed_registry(conn)
    whole = _ingest(conn, corpus)

    monkeypatch.setattr(spl_run, "WORDING_CHUNK", chunk)
    chunked = _ingest(conn, corpus)

    assert chunked.occurrences == whole.occurrences
    assert chunked.quotes == whole.quotes
    assert chunked.quoted_chars == whole.quoted_chars
    assert chunked.wordings_with_a_moiety == whole.wordings_with_a_moiety
    assert chunked.pairs == whole.pairs


@pytest.mark.usefixtures("_clean")
def test_the_measured_pair_floor_is_asserted_when_it_is_given(conn, corpus):
    """The floor asserts `>=`, and this fixture cannot reach the real one -- so
    the check is shown REFUSING rather than assumed to work on a corpus that
    happens to clear it."""
    _seed_registry(conn)
    with pytest.raises(ValueError, match="below the measured floor"):
        _ingest(conn, corpus, pair_floor=spl_run.MEASURED_PAIR_FLOOR)


@pytest.mark.usefixtures("_clean")
def test_the_measured_NOVEL_floor_is_asserted_when_it_is_given(conn, corpus):
    """The pair floor's twin, and the reason it exists separately.

    ⇒ THIS PINS THE CONSTANT, not just the branch. `MEASURED_NOVEL_FLOOR` could
    be set to 1 with every other test in the suite green: the unit tests above
    pass their own floor values, and the CLI test compares the wiring against
    the constant itself, which is tautological in the constant's value. Only a
    real ingest that CANNOT clear the published figure notices that the figure
    moved -- which is exactly how the pair floor is pinned four lines up.
    """
    _seed_registry(conn)
    with pytest.raises(ValueError, match="novel pairs, below the measured"):
        _ingest(conn, corpus, novel_floor=spl_run.MEASURED_NOVEL_FLOOR)


@pytest.mark.usefixtures("_clean")
def test_a_refused_floor_rolls_the_WHOLE_run_back(conn, corpus):
    """A half-written projection is worse than none."""
    _seed_registry(conn)
    with pytest.raises(ValueError):
        _ingest(conn, corpus, pair_floor=spl_run.MEASURED_PAIR_FLOOR)
    (labels,) = conn.execute(
        "SELECT count(*) FROM drugref.spl_label").fetchone()
    (unfinished,) = conn.execute(
        "SELECT count(*) FROM drugref.ingest_run "
        " WHERE source = 'SPL' AND finished_at IS NULL").fetchone()
    assert labels == 0
    # The run row SURVIVES the rollback and says it never finished -- that is
    # provenance.open_run's early commit doing its job, not a leak.
    assert unfinished == 1


@pytest.mark.parametrize("counter", [
    "dropped_no_set_id_bytes", "dropped_unreadable", "dropped_prefilter_disagreed",
    "dropped_no_xml_member", "dropped_several_xml_members",
    "dropped_untrustworthy_prefilter", "dropped_junk_version",
    "dropped_unknown_class_code_unii",
])
def test_a_scan_that_dropped_a_document_for_a_READING_reason_is_refused(counter):
    """A drop here is republished as `absent_from_dailymed` -- a fact about this
    code sold as a fact about the release.

    Parametrised over EVERY counter rather than asserted on one, because the two
    that were missing were missing precisely where no test looked.
    """
    with pytest.raises(ValueError, match="republished as 'absent from DailyMed'"):
        spl_checks.check_scan_dropped_nothing(_scan(**{counter: 2}))


def test_a_clean_scan_passes_the_drop_check():
    """The control: without it every refusal above could be an always-raising
    guard."""
    spl_checks.check_scan_dropped_nothing(_scan())


def test_a_member_that_was_never_a_label_is_NOT_counted_as_a_drop():
    """An outer member that is not a zip -- a manifest, an index -- was never a
    label container, so calling it a lost label would be the reader-versus-release
    confusion running in the other direction. Counted, reported, not refused."""
    spl_checks.check_scan_dropped_nothing(_scan(skipped_not_a_member_zip=3))


# --------------------------------------------------------------------------
# The summary type's own contract
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# THE FLOORS -- every one of them watched refusing
# --------------------------------------------------------------------------
#
# ⇒ THE NOVEL FLOOR WAS COMPLETELY UNTESTED. Setting MEASURED_NOVEL_FLOOR to 1
# left the suite green, and so did replacing its whole comparison with `if
# False:` -- while `cli_spl` gates BOTH measured floors off the single
# `--no-pair-floor` flag, so the shipped path asserted a floor no test had ever
# seen refuse anything. The five structural floors were in the same position:
# `if value == 0:` -> `if False:` passed, because no fixture ever produced a zero.

def test_check_floors_ACCEPTS_a_summary_that_published_something():
    """The control. Without it every refusal below could be an always-raising
    guard -- which is the failure this whole section exists to rule out."""
    spl_checks.check_floors(_summary(), pair_floor=15, novel_floor=14)


@pytest.mark.parametrize("field,overrides", [
    ("labels", {"labels": 0, "labels_by_route": {}, "dailymed_targets": 0,
                "dailymed_found": 0}),
    ("wordings", {"wordings": 0, "wordings_with_a_moiety": 0}),
    ("resolved subjects", {"labels_by_route": {"unresolved": 10}}),
    ("entity occurrences", {"occurrences": 0}),
    ("candidate pairs", {"pairs": 0}),
])
def test_a_structural_floor_of_zero_is_REFUSED(field, overrides):
    """An all-zeros run is perfectly self-consistent -- `stored == written` four
    times over -- and `clear_source_spl` has already deleted the previous
    release. Every reconciliation in the slice passes on it, so these five are
    the only thing standing between an empty read and a reported success.

    Each override keeps `SplSummary`'s OWN invariants satisfied -- zeroing
    `labels` alone trips the route-sum contract first, which would make this a
    test of the summary type rather than of the floor.
    """
    with pytest.raises(ValueError, match=f"published 0 {field}"):
        spl_checks.check_floors(_summary(**overrides),
                                pair_floor=None, novel_floor=None)


def test_a_pair_count_below_the_MEASURED_floor_is_REFUSED():
    with pytest.raises(ValueError, match="below the measured floor"):
        spl_checks.check_floors(_summary(pairs=14), pair_floor=15,
                                novel_floor=None)


def test_a_NOVEL_pair_count_below_the_measured_floor_is_REFUSED():
    """The floor `cli_spl` asserts on every production run and no test had ever
    watched refuse. It is a separate figure from the pair floor -- novelty is
    measured against `exact_ddi_pair` AND `ddi_candidate_pair` -- so a run can
    clear the pair floor and fail this one."""
    with pytest.raises(ValueError, match="novel pairs, below the measured"):
        spl_checks.check_floors(_summary(pairs=15, novel_pairs=13),
                                pair_floor=15, novel_floor=14)


def test_the_measured_floors_are_OPTIONAL_and_the_structural_ones_are_not():
    """A partial corpus has to be able to say so, which is why the two measured
    floors are `None`-able. The structural five never are."""
    spl_checks.check_floors(_summary(pairs=1, novel_pairs=1),
                            pair_floor=None, novel_floor=None)


def _summary(**overrides):
    fields = dict(
        records_read=100, labels=10, wordings=4,
        labels_by_route={"openfda_unii": 6, "unresolved": 4},
        dailymed_targets=4, dailymed_documents_read=50, dailymed_found=3,
        dailymed_reported_skips="",
        occurrences=40, wordings_with_a_moiety=3, quotes=12, quoted_chars=900,
        quotable_chars=1000, self_pairs=2, pairs=15, novel_pairs=14)
    return spl_checks.SplSummary(**(fields | overrides))


def test_the_summary_refuses_to_exist_unless_the_route_buckets_sum():
    with pytest.raises(ValueError, match="route buckets sum"):
        _summary(labels_by_route={"openfda_unii": 6})


def test_the_summary_refuses_a_route_outside_db051s_vocabulary():
    with pytest.raises(ValueError, match="not in the vocabulary"):
        _summary(labels_by_route={"openfda_unii": 6, "guessed_from_name": 4})


def test_the_summary_refuses_more_quoted_characters_than_the_budget_allows():
    """⇒ The licensing determination in one assertion.

    `quoted_chars` is summed over the windows written and `quotable_chars` over
    each wording's independently-computed budget, so this compares two
    quantities derived by different routes -- unlike the bucket identities,
    which hold by construction at the call site.
    """
    with pytest.raises(ValueError, match="issue 154"):
        _summary(quoted_chars=1_001, quotable_chars=1_000)


def test_the_summary_refuses_finding_more_labels_than_it_looked_for():
    with pytest.raises(ValueError, match="different populations"):
        _summary(dailymed_targets=2, dailymed_found=3)


def test_the_summary_refuses_more_naming_wordings_than_wordings():
    with pytest.raises(ValueError, match="but only"):
        _summary(wordings=2, wordings_with_a_moiety=3)


def test_a_well_formed_summary_is_accepted():
    """The control, again: five refusals above prove nothing without it."""
    assert _summary().resolved_labels == 6


# --------------------------------------------------------------------------
# The reported skips: counted, printed, and now SURVIVING the call
# --------------------------------------------------------------------------

@pytest.mark.usefixtures("_clean")
def test_a_reported_skip_REACHES_the_summary_not_only_the_terminal(
        conn, corpus, monkeypatch):
    """⇒ DELETING THE REPORT LINE USED TO LEAVE THE WHOLE SUITE GREEN.

    `describe_reported_skips` is thoroughly unit-tested and nothing asserted
    that anybody CALLS it. Worse, the only call went through `say()`, which is a
    no-op whenever `progress` is None -- the default, and what every library
    caller and every test uses -- so the counter survived in the scrollback of an
    interactive run and nowhere else. That is the "counted and reported, reported
    nowhere" defect this round exists to close, one level up from where it was
    found.
    """
    real_scan = spl_release.scan_release

    def scan_with_a_reported_skip(*args, **kwargs):
        return dataclasses.replace(
            real_scan(*args, **kwargs), skipped_unknown_class_code=7,
            unknown_class_codes=frozenset({"ZZZZ"}))

    _seed_registry(conn)
    monkeypatch.setattr(spl_run.spl_release, "scan_release",
                        scan_with_a_reported_skip)
    summary = _ingest(conn, corpus)

    assert "7" in summary.dailymed_reported_skips
    assert "ZZZZ" in summary.dailymed_reported_skips, (
        "the code is what a human has to rule on; the count alone is unactionable")
    assert "ZZZZ" in str(summary)


@pytest.mark.usefixtures("_clean")
def test_a_clean_scan_adds_NO_skip_clause_to_the_summary(conn, corpus):
    """The control. A clause printed on every run reading "none" is one nobody
    reads, which is how the previous counter went unnoticed for a whole slice."""
    _seed_registry(conn)
    summary = _ingest(conn, corpus)

    assert summary.dailymed_reported_skips == ""
    assert "reported and NOT refused" not in str(summary)
