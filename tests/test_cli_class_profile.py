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


def test_drug_renderer_shows_all_four_groups():
    lens = cpr.DrugLens(_U, "K class", ((_line(), "inherits via P"),), (_line("Gout"),),
                        (_line("Asthma2"),), (_line("Gout2"),))
    out = "\n".join(ccp.render_drug_profile(
        cpr.DrugProfile(_U, "adrug", ((_U, "K class"),), (lens,), ())))
    assert "inherits via P" in out and "peculiar: CI_with condition Gout" in out
    assert "not confirmed for this drug: CI_with condition Asthma2" in out
    assert "confirmed exception: CI_with condition Gout2" in out
    low = out.lower().replace("confirmed exception", "")
    assert "safe" not in low and "does not apply" not in low


def test_drug_renderer_shows_own_properties_without_a_class_tag():
    out = "\n".join(ccp.render_drug_profile(
        cpr.DrugProfile(_U, "loner", (), (), (_line("Gout"),))))
    assert "own properties (no class tag):" in out and "CI_with condition Gout" in out


def _wire(conn, monkeypatch):
    """Run the CLI on the test's own connection; keep the rollback isolation. Returns
    the list each (stubbed) commit appends to, so a test can see the CLI committed."""
    commits = []
    monkeypatch.setattr(cli.db, "connect", lambda dsn: contextlib.nullcontext(conn))
    monkeypatch.setattr(conn, "commit", lambda: commits.append(True))
    return commits


def test_profile_class_and_drug_through_cli_main(conn, monkeypatch, capsys):
    _wire(conn, monkeypatch)
    r = w.run(conn)
    k = w.klass(conn, r, "Cli class")
    c = w.condition(conn, r, "Clicond")
    ms = [w.moiety(conn, r, n) for n in ("cli1", "cli2", "cli3", "cli4")]
    for m in ms:
        w.member(conn, r, m, k)
    for m in ms[:3]:                               # 3 of 4: observed
        w.contraindication(conn, r, m, c)
    assert cli.main(["--dsn", "x", "profile", "class", str(k)]) == 0
    assert "not confirmed for: cli4" in capsys.readouterr().out
    assert cli.main(["--dsn", "x", "profile", "drug", str(ms[3])]) == 0
    assert "not confirmed for this drug: CI_with condition Clicond" in \
        capsys.readouterr().out
    assert cli.main(["--dsn", "x", "profile", "drug", str(ms[3]), "--lens", str(k)]) == 0
    capsys.readouterr()
    for argv in (["class", str(uuid.uuid4())], ["drug", str(uuid.uuid4())],
                 ["drug", str(ms[3]), "--lens", str(uuid.uuid4())]):
        assert cli.main(["--dsn", "x", "profile", *argv]) == 2
        out = capsys.readouterr()
        assert out.out == "" and out.err.startswith("drugref: ")  # errors to stderr


def test_profile_rule_with_a_bad_verdict_exits_2(conn, monkeypatch, capsys):
    _wire(conn, monkeypatch)
    r = w.run(conn)
    m, c = w.moiety(conn, r, "bdrug"), w.condition(conn, r, "Bcond")
    rc = cli.main(["--dsn", "x", "profile", "rule", "--moiety", str(m),
                   "--relationship", "CI_with", "--object-kind", "condition",
                   "--object", str(c), "--verdict", "maybe", "--reviewed-by", "t",
                   "--reviewed-against", "t"])
    assert rc == 2
    assert capsys.readouterr().err.startswith("drugref: ")


def test_profile_rule_writes_through_the_writer(conn, monkeypatch, capsys):
    r = w.run(conn)
    m, c = w.moiety(conn, r, "rdrug"), w.condition(conn, r, "Rcond")
    commits = _wire(conn, monkeypatch)
    argv = ["--dsn", "x", "profile", "rule", "--moiety", str(m),
            "--relationship", "CI_with", "--object-kind", "condition",
            "--object", str(c), "--verdict", "holds", "--reviewed-by", "t",
            "--reviewed-against", "t"]
    assert cli.main(argv) == 0
    assert "recorded" in capsys.readouterr().out
    assert commits == [True]
    assert conn.execute(
        "SELECT count(*) FROM drugref.curated_property_ruling "
        "WHERE subject_moiety_uuid = %s AND verdict = 'holds'", (m,)).fetchone()[0] == 1
    # The same answer again writes nothing, and says so instead of "recorded".
    assert cli.main(argv) == 0
    assert "already live (unchanged)" in capsys.readouterr().out


def test_a_class_does_not_hold_warns_what_it_does_not_do(conn, monkeypatch, capsys):
    """A class-subject does_not_hold only withdraws the class's own holds; a curator
    trying to refute an observed property must be told it still stands."""
    r = w.run(conn)
    k, c = w.klass(conn, r, "Wclass"), w.condition(conn, r, "Wcond")
    _wire(conn, monkeypatch)
    assert cli.main(["--dsn", "x", "profile", "rule", "--class", str(k),
                     "--relationship", "may_treat", "--object-kind", "condition",
                     "--object", str(c), "--verdict", "does_not_hold",
                     "--reviewed-by", "t", "--reviewed-against", "t"]) == 0
    assert "only withdraws" in capsys.readouterr().err
