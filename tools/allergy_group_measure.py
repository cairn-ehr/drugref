# tools/allergy_group_measure.py
"""Measure the allergy groups on a real database (spec section 6). READ-ONLY.

A measurement, not a gate: it exits 0 whatever the counts. Prints member count,
placement coverage, every -cillin moiety the penicillin group does not reach (with
the reason), and the MED-RT has_SC -> Penicillins recall cross-check.

Usage:
    uv run python -m tools.allergy_group_measure --dsn "host=... dbname=..." \
        --medrt downloads/MEDRT/Core_MEDRT_2026.07.06_XML.xml [--timing 200]
"""

import argparse
import statistics
import sys
import time
import xml.etree.ElementTree as ET

from drugref import db, server_messages


def has_sc_penicillin_rxcuis(path) -> list[tuple[str, str]]:
    """(rxcui, name) for each RxNorm has_SC association whose target is 'Penicillins'.

    Streams the file (iterparse, clearing each association) so the 45 MB release is
    never held in memory.
    """
    found = []
    for _, el in ET.iterparse(path):
        if el.tag != "association":
            continue
        if (
            el.findtext("name") == "has_SC"
            and el.findtext("from_namespace") == "RxNorm"
            and el.findtext("to_name") == "Penicillins"
        ):
            found.append((el.findtext("from_code"), el.findtext("from_name")))
        el.clear()
    return found


def _check(conn, prescribed, allergen):
    row = conn.execute(
        "SELECT outcome, reason FROM drugref.allergy_check(%s, %s) LIMIT 1",
        (prescribed, allergen),
    ).fetchone()
    return row


def _scalar(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def report(conn, medrt_path) -> None:
    group = _scalar(
        conn, "SELECT class_uuid FROM drugref.allergy_group LIMIT 1"
    )  # one group today
    n_members = _scalar(
        conn,
        "SELECT count(DISTINCT moiety_uuid) FROM drugref.allergy_group_member "
        "WHERE class_uuid=%s",
        group,
    )
    print("== members")
    print(f"penicillin group {group}: {n_members} distinct member moieties")
    for via, n in conn.execute(
        "SELECT via, count(*) FROM drugref.allergy_group_member "
        "WHERE class_uuid=%s GROUP BY via ORDER BY via",
        (group,),
    ):
        print(f"  via {via}: {n}")

    print("== placed")
    total = _scalar(conn, "SELECT count(*) FROM drugref.substance_moiety")
    placed = _scalar(conn, "SELECT count(*) FROM drugref.allergy_mesh_placement")
    presc = (
        "(SELECT DISTINCT moiety_uuid FROM drugref.identity_claim "
        "WHERE scheme='RXNORM_IN' AND superseded_by IS NULL)"
    )
    n_presc = _scalar(conn, f"SELECT count(*) FROM {presc} x")
    placed_presc = _scalar(
        conn,
        f"SELECT count(*) FROM {presc} x JOIN "
        "drugref.allergy_mesh_placement p USING (moiety_uuid)",
    )
    print(f"all moieties: {placed} of {total} placed ({placed / max(total, 1):.1%})")
    print(
        f"prescribable (unsuperseded RXNORM_IN): {placed_presc} of {n_presc} placed "
        f"({placed_presc / max(n_presc, 1):.1%}); "
        f"{n_presc - placed_presc} would answer unplaced"
    )

    print("== -cillin moieties not in the group")
    rows = conn.execute(
        "SELECT moiety_uuid, display_name FROM drugref.substance_moiety "
        "WHERE display_name ILIKE '%%cillin%%' ORDER BY display_name"
    ).fetchall()
    missed = 0
    for m, name in rows:
        outcome, reason = _check(conn, m, group)
        if outcome != "match":
            missed += 1
            print(
                f"  {name}: {outcome}"
                + (f" ({reason})" if reason else " (placed, outside the group)")
            )
    print(f"{missed} of {len(rows)} -cillin moieties not reached")

    print("== MED-RT has_SC -> Penicillins")
    assoc = has_sc_penicillin_rxcuis(medrt_path)
    misses, no_moiety = [], []
    for rxcui, name in assoc:
        ms = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT moiety_uuid FROM drugref.identity_claim "
                "WHERE scheme='RXNORM_IN' "
                "AND value=%s AND superseded_by IS NULL",
                (rxcui,),
            )
        ]
        if not ms:
            no_moiety.append((rxcui, name))
        elif not any(_check(conn, m, group)[0] == "match" for m in ms):
            misses.append((rxcui, name))
    print(
        f"{len(assoc)} associations; "
        f"{len(assoc) - len(no_moiety)} resolved to a moiety; "
        f"{len(misses)} misses; {len(no_moiety)} RxCUIs with no moiety"
    )
    for rxcui, name in misses:
        print(f"  MISS {rxcui} {name}")
    for rxcui, name in no_moiety:
        print(f"  NO-MOIETY {rxcui} {name}")


def timing(conn, n: int) -> None:
    """Median/p95 per allergy_check call over n calls of a mixed set (warm)."""
    print("== timing")
    ms = [
        r[0]
        for r in conn.execute(
            "SELECT moiety_uuid FROM drugref.substance_moiety "
            "ORDER BY moiety_uuid LIMIT 50"
        )
    ]
    group = _scalar(conn, "SELECT class_uuid FROM drugref.allergy_group LIMIT 1")
    calls = [
        (ms[i % len(ms)], group if i % 2 else ms[(i + 7) % len(ms)]) for i in range(n)
    ]
    for c in calls[:20]:  # warm
        _check(conn, *c)
    t = []
    for c in calls:
        s = time.perf_counter()
        _check(conn, *c)
        t.append((time.perf_counter() - s) * 1000)
    t.sort()
    print(
        f"{n} calls: median {statistics.median(t):.2f} ms, "
        f"p95 {t[int(0.95 * n) - 1]:.2f} ms, max {t[-1]:.2f} ms"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dsn", help="PostgreSQL DSN (default: $DRUGREF_DSN)")
    ap.add_argument("--medrt", required=True, help="MED-RT Core XML path")
    ap.add_argument(
        "--timing", type=int, default=0, help="also time N allergy_check calls"
    )
    args = ap.parse_args(argv)
    with db.connect(args.dsn) as conn:
        report(conn, args.medrt)
        if args.timing:
            timing(conn, args.timing)
    return 0


if __name__ == "__main__":
    server_messages.configure_logging()
    sys.exit(main())
