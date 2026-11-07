"""`drugref profile` -- pure renderers (no DB) and the ruling write path (DB-gated)."""
import contextlib
import uuid

from drugref import class_profile_read as cpr
from drugref import cli, cli_class_profile as ccp
from tests import class_profile_world as w

_U = uuid.UUID(int=1)


def _line(name="Asthma"):
    return cpr.PropertyLine("CI_with", "condition", _U, name)


def _profile():
    p = cpr.ClassProperty(_line(), True, ("K class",), ("MED-RT",), True, 3, 4,
                          ("edrug",), ("fdrug",))
    return cpr.ClassProfile(_U, "K class", "MED-RT", "EPC", ("adrug", "edrug"), (p,),
                            {"adrug": (_line("Gout"),)}, (("ddrug", "S class"),))


def test_class_renderer_uses_the_exact_copy():
    text = "\n".join(ccp.render_class_profile(_profile()))
    for s in ("not confirmed for: edrug", "confirmed exception: fdrug",
              "in subclasses only:", "asserted (MED-RT)", "observed in 3 of 4 members"):
        assert s in text
    low = text.lower().replace("confirmed exception", "")
    assert "safe" not in low and "exception" not in low and "does not apply" not in low


def test_drug_renderer_shows_shared_and_peculiar():
    lens = cpr.DrugLens(_U, "K class", ((_line(), "inherits via P"),), (_line("Gout"),))
    out = "\n".join(ccp.render_drug_profile(
        cpr.DrugProfile(_U, "adrug", ((_U, "K class"),), (lens,))))
    assert "inherits via P" in out and "Gout" in out and "adrug" in out


def test_profile_rule_writes_through_the_writer(conn, monkeypatch, capsys):
    r = w.run(conn)
    m, c = w.moiety(conn, r, "rdrug"), w.condition(conn, r, "Rcond")
    # run the CLI on the test's own connection; keep the rollback isolation
    monkeypatch.setattr(cli.db, "connect", lambda dsn: contextlib.nullcontext(conn))
    monkeypatch.setattr(conn, "commit", lambda: None)
    rc = cli.main(["--dsn", "x", "profile", "rule", "--moiety", str(m),
                   "--relationship", "CI_with", "--object-kind", "condition",
                   "--object", str(c), "--verdict", "holds", "--reviewed-by", "t",
                   "--reviewed-against", "t"])
    assert rc == 0
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_property_ruling "
        "WHERE subject_moiety_uuid = %s AND verdict = 'holds'", (m,)).fetchone()[0] == 1
