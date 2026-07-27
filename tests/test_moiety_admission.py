# tests/test_moiety_admission.py
"""DB-gated tests for drugref.moiety_admission -- the record of WHY each moiety
is in the registry (#26, db/011).

The gate stopped being a single boolean when #26 replaced INN_ID-only with
`INN | USAN | (RXCUI & drug-like type) | allow-list`. Storing which signal fired
makes the decision auditable across releases and lets a curation worklist be
ordered by evidence strength (#19) instead of by nothing.
"""
import pathlib

import pytest

from drugref import ids
from drugref.ingest import run

FIX = pathlib.Path(__file__).parent / "fixtures" / "unii_subset.tsv"
DATA = pathlib.Path("src/drugref/data")
XW = DATA / "usan_inn_crosswalk.tsv"
AL = DATA / "legacy_allowlist.tsv"

# Real UNIIs from the extracted fixture, chosen one per branch of the gate rule.
PARACETAMOL = "362O9ITL9D"        # INN_ID + RXCUI  -> two signals
AMOXICILLIN = "804826J2HU"        # RXCUI only      -> the weakest evidence
IRON_SUCROSE = "FZ7NYF5N8L"       # USAN_ID only
MAGNESIUM = "DE08037SAB"          # LEGACY_ALLOWLIST only
POLYSORBATE = "6OZP39ZG8H"        # rejected entirely


@pytest.fixture(autouse=True)
def _clean(conn):
    # ingest_unii commits internally, so the conn fixture's rollback cannot
    # isolate these tests.
    conn.execute("TRUNCATE drugref.moiety_admission, drugref.identity_claim, "
                 "drugref.substance_moiety, drugref.ingest_run "
                 "RESTART IDENTITY CASCADE")
    conn.commit()
    yield


def _ingest(conn, release="2026-07"):
    return run.ingest_unii(conn, unii_path=FIX, crosswalk_path=XW,
                           allowlist_path=AL, upstream_release=release)


def _signals(conn, unii_code):
    rows = conn.execute(
        "SELECT signal FROM drugref.moiety_admission WHERE moiety_uuid = %s "
        "ORDER BY signal", (ids.mint_moiety_uuid(unii_code),)).fetchall()
    return [r[0] for r in rows]


def test_the_admitting_signal_is_recorded(conn):
    _ingest(conn)
    assert _signals(conn, AMOXICILLIN) == ["RXCUI"]
    assert _signals(conn, IRON_SUCROSE) == ["USAN_ID"]
    assert _signals(conn, MAGNESIUM) == ["LEGACY_ALLOWLIST"]


def test_evidence_is_set_valued(conn):
    """Paracetamol carries an INN_ID AND an RxCUI, so BOTH are recorded.

    Storing only the strongest would make "this moiety rests on one weak signal"
    indistinguishable from "this moiety is corroborated twice" -- which is
    exactly the question the table exists to answer.
    """
    _ingest(conn)
    assert _signals(conn, PARACETAMOL) == ["INN_ID", "RXCUI"]


def test_a_rejected_substance_has_no_evidence_row(conn):
    _ingest(conn)
    assert _signals(conn, POLYSORBATE) == []


def test_every_admitted_moiety_has_at_least_one_signal(conn):
    """Conservation: no moiety may be in the registry unexplained.

    Same spirit as the MeSH no-silent-drop test -- a moiety with no evidence row
    would be one the gate admitted for a reason nothing recorded.
    """
    summary = _ingest(conn)
    unexplained = conn.execute(
        "SELECT count(*) FROM drugref.substance_moiety m "
        "WHERE NOT EXISTS (SELECT 1 FROM drugref.moiety_admission a "
        "                  WHERE a.moiety_uuid = m.moiety_uuid)").fetchone()[0]
    assert unexplained == 0
    distinct = conn.execute("SELECT count(DISTINCT moiety_uuid) "
                            "FROM drugref.moiety_admission").fetchone()[0]
    assert distinct == summary.moieties


def test_evidence_is_rebuilt_not_accumulated(conn):
    """A projection, not an append-only overlay.

    The moiety is immortal; the EVIDENCE is a per-release observation. If a
    future release stops populating a substance's INN_ID, the moiety must stay
    (its UUID is cited) while that evidence row must be able to go -- otherwise
    the table would keep asserting something the current release does not say.
    """
    first = _ingest(conn)
    _ingest(conn, release="2026-08")
    rows = conn.execute("SELECT count(*) FROM drugref.moiety_admission").fetchone()[0]
    per_moiety = conn.execute(
        "SELECT count(*) FROM (SELECT moiety_uuid, signal FROM drugref.moiety_admission "
        "                      GROUP BY 1, 2 HAVING count(*) > 1) d").fetchone()[0]
    assert per_moiety == 0                      # no duplicates from the second run
    assert rows >= first.moieties               # still at least one signal each


def test_the_weakest_evidence_is_queryable(conn):
    """The #19 worklist query: which moieties rest on RXCUI alone?

    Being able to write this in one statement is the reason the table is
    set-valued and relational rather than a column on substance_moiety.
    """
    _ingest(conn)
    weakest = {r[0] for r in conn.execute(
        "SELECT moiety_uuid FROM drugref.moiety_admission "
        "GROUP BY moiety_uuid HAVING array_agg(signal ORDER BY signal) = ARRAY['RXCUI']"
    ).fetchall()}
    assert ids.mint_moiety_uuid(AMOXICILLIN) in weakest
    assert ids.mint_moiety_uuid(PARACETAMOL) not in weakest      # corroborated by INN


def test_an_unknown_signal_is_refused(conn, a_moiety, ingest_run_id):
    """The vocabulary is closed: a typo'd signal must not become a silent
    fourth category that every GROUP BY then under-reports."""
    with pytest.raises(Exception):
        conn.execute("INSERT INTO drugref.moiety_admission "
                     "(moiety_uuid, signal, ingest_run) VALUES (%s, 'INN', %s)",
                     (a_moiety, ingest_run_id))
