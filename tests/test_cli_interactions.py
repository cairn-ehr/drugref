"""`drugref interactions` -- the consumer `effective_grades_for` did not have (issue
114). DB-gated.

WHY THIS COMMAND EXISTS AT ALL. `curated_read.py`'s module docstring argues that "a view
with no consumer is half a feature", citing the two views this project shipped that way
before writing the rule down. db/037 gave `curated_ddi_pair_effective` a caller --
`effective_grades_for` -- and the caller then had none itself: `grep -rn
effective_grades_for src tests` found its own definition and one test module. The
standard the file sets for the view is one it did not meet.

AND THE HALF THE ISSUE SAID WAS THE REAL POINT: a CLI is where DIRECTIONALITY first
bites a real user. db/006's convention is that a rule stated as (X, Y) does not answer
(Y, X), and `effective_grades_for` deliberately does NOT union the two -- "folding the
mirror in here would hide from the caller that two lookups happened". A command that
asks about ONE drug can only ever show one direction; a command asking whether TWO drugs
interact must do both lookups and be seen to do them. Both forms are driven here.
"""
import uuid

import pytest

from drugref import cli, cli_interactions, curation
from tests.test_class_subject_read_path import (_a_graded_class_rule, _a_moiety,
                                                _file_member)
from tests.test_curated_overlay import _a_class


def _a_graded_pair(conn, ingest_run_id, *, subject_code, object_code, severity,
                   subject_unii, partner_unii, subject_name, partner_name,
                   mechanism=None, management=None):
    """One MOIETY-grain rule: subject drug -> object class, graded, with the partner
    filed under that class. The ordinary shape -- 255 of the reference database's
    curated pairs are moiety-grain and none are graded by both grains."""
    object_class = _a_class(conn, ingest_run_id, code=object_code, name=subject_code)
    subject = _a_moiety(conn, ingest_run_id, subject_unii, subject_name)
    partner = _a_moiety(conn, ingest_run_id, partner_unii, partner_name)
    _file_member(conn, partner, object_class, ingest_run_id)
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)",
        (subject, object_class, ingest_run_id))
    curation.record_interaction_judgement(
        conn, subject, object_class, "CI_MoA", True, severity=severity,
        evidence_grade="established", mechanism=mechanism, management=management,
        reviewed_by="a curator", reviewed_against="2026.07.06")
    return subject, partner


# ============================================================================
# 1. the command exists and reads the view that applies the precedence
# ============================================================================


def test_it_prints_the_partner_its_grade_and_which_grain_decided(conn, ingest_run_id,
                                                                 capsys):
    """THE HEADLINE. Everything a prescribing client needs off one lookup: who the
    partner is, how bad it is, and whether the grade names this actual drug or its
    whole class -- `rule_grain` is on `GradedPair` precisely so a consumer can tell
    those apart, and a command that dropped it would publish a class-wide generality
    as though it were about the drug in front of the user."""
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000010100", object_code="N0000010200",
        severity="major", subject_unii="TESTUNIIJ1", partner_unii="TESTUNIIJ2",
        subject_name="the subject drug", partner_name="the partner drug",
        mechanism="additive QT prolongation",
        management="avoid; if unavoidable, ECG before and 4h after the second dose")

    assert cli_interactions._handle_interactions(conn, _args(subject)) == 0
    out = capsys.readouterr().out
    assert str(partner) in out
    assert "major" in out
    assert "moiety_rule" in out
    assert "additive QT prolongation" in out
    # BOTH FREE-TEXT FIELDS, because they are printed by one loop over a two-tuple and
    # asserting only `mechanism` left dropping `management` from it a survivor -- the
    # PR #119 review measured that. Management is the ACTIONABLE half of a grade: a
    # clinician told two drugs interact and not what to do has been warned, not helped.
    assert "ECG before and 4h after the second dose" in out


def test_the_most_severe_partner_is_listed_first(conn, ingest_run_id, capsys):
    """THE ORDER IS THE POINT OF THE READ, not a presentation flourish: an operator or
    a clinician reading the head of the list must see the most concerning partner, which
    is the whole reason `effective_grades_for` orders by `effective_rank`."""
    subject, worse = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000010300", object_code="N0000010400",
        severity="contraindicated", subject_unii="TESTUNIIJ3",
        partner_unii="TESTUNIIJ4", subject_name="the subject drug",
        partner_name="the dangerous partner")
    milder_class = _a_class(conn, ingest_run_id, code="N0000010500",
                            name="Milder object [MoA]")
    milder = _a_moiety(conn, ingest_run_id, "TESTUNIIJ5", "the milder partner")
    _file_member(conn, milder, milder_class, ingest_run_id)
    conn.execute(
        "INSERT INTO drugref.class_contraindication (subject_moiety_uuid, "
        "object_class_uuid, relationship, source, ingest_run) "
        "VALUES (%s, %s, 'CI_MoA', 'MED-RT', %s)",
        (subject, milder_class, ingest_run_id))
    curation.record_interaction_judgement(
        conn, subject, milder_class, "CI_MoA", True, severity="minor",
        evidence_grade="established", reviewed_by="a curator",
        reviewed_against="2026.07.06")

    cli_interactions._handle_interactions(conn, _args(subject))
    out = capsys.readouterr().out
    assert out.index(str(worse)) < out.index(str(milder)), (
        "contraindicated must precede minor -- a client reading the head of this list "
        "is reading it for exactly that reason")


def test_a_moiety_with_no_grades_is_an_ordinary_answer(conn, ingest_run_id, capsys):
    """NOT AN ERROR, AND NOT SILENCE. The overlay is small and deliberately so, so most
    moieties carry no curated grade -- and `effective_grades_for`'s docstring is
    explicit that this is indistinguishable from a drug nobody has heard of. The command
    must not imply it checked something it did not.
    """
    ungraded = _a_moiety(conn, ingest_run_id, "TESTUNIIJ6", "an ungraded drug")

    assert cli_interactions._handle_interactions(conn, _args(ungraded)) == 0
    out = capsys.readouterr().out
    assert "no curated grade" in out
    # THE QUALIFIER, NAMED. This asserted `"not" in out.lower()` until the PR #119
    # review, which is satisfied by the word "note:" in the directional disclaimer -- so
    # it passed on any output containing that line and pinned nothing at all. What the
    # test means is that the empty answer arrives QUALIFIED rather than bare.
    assert "directional" in out.lower(), (
        "an empty list with no qualifier reads as 'these do not interact', which is a "
        "claim drugref has not made -- this form searched ONE direction")


def test_an_unknown_uuid_is_not_an_error(conn, capsys):
    """SAME ANSWER AS AN UNGRADED DRUG, and the command says so rather than inventing a
    distinction the view cannot support: this view's population is GRADES, not drugs, so
    it genuinely cannot tell the two apart. A caller needing that asks
    `substance_moiety`."""
    assert cli_interactions._handle_interactions(conn, _args(uuid.UUID(int=0))) == 0
    assert "no curated grade" in capsys.readouterr().out


# ============================================================================
# 2. directionality -- the contract a CLI is the first place to feel (issue 114)
# ============================================================================


def test_one_moiety_shows_only_the_direction_drugref_holds(conn, ingest_run_id,
                                                           capsys):
    """db/006's CONVENTION, made visible. A rule stated as (X, Y) does not answer
    (Y, X), so asking about the PARTNER of a rule returns nothing -- and a user who did
    not know that would read the empty answer as "these do not interact".

    This is the defect the issue predicted a CLI would surface first, and the reason
    the command's output names the direction it searched rather than leaving it implied.
    """
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000010600", object_code="N0000010700",
        severity="major", subject_unii="TESTUNIIJ7", partner_unii="TESTUNIIJ8",
        subject_name="the subject drug", partner_name="the partner drug")

    cli_interactions._handle_interactions(conn, _args(subject))
    assert str(partner) in capsys.readouterr().out

    cli_interactions._handle_interactions(conn, _args(partner))
    out = capsys.readouterr().out
    assert str(subject) not in out, (
        "the premise: drugref holds this rule in ONE direction, and the reverse lookup "
        "genuinely finds nothing")
    assert "directional" in out.lower(), (
        "so the command must SAY so -- an unqualified empty answer here reads as "
        "'these two do not interact', which drugref has not asserted")


def test_asking_about_two_drugs_queries_both_directions(conn, ingest_run_id, capsys):
    """THE PAIR FORM, and it is what `effective_grades_for`'s docstring prescribes:
    "a client asking 'do these two interact' queries BOTH directions".

    TWO LOOKUPS, NOT A UNIONED VIEW. The module deliberately refuses to fold the mirror
    in, because that would hide from the caller that two lookups happened; the command
    is the caller, so it does both and finds the rule WHICHEVER way round the user names
    the drugs. Driven both ways here -- one direction alone would pass a command that
    only ever looked up its first argument.
    """
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000010800", object_code="N0000010900",
        severity="contraindicated", subject_unii="TESTUNIIK1",
        partner_unii="TESTUNIIK2", subject_name="the subject drug",
        partner_name="the partner drug")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    assert "contraindicated" in capsys.readouterr().out

    assert cli_interactions._handle_interactions(conn, _args(partner, subject)) == 0
    assert "contraindicated" in capsys.readouterr().out, (
        "the rule is stated (subject, partner); naming them the other way round must "
        "still find it, which is the whole reason the pair form does two lookups")


def test_the_pair_form_reports_only_the_pair_asked_about(conn, ingest_run_id, capsys):
    """THE FILTER ON `partner_moiety`, which nothing pinned until the PR #119 review.

    `effective_grades_for` returns EVERY graded partner of the subject, so the pair form
    filters each lookup down to the other drug. Replacing that comprehension with an
    unfiltered `list(...)` left the whole suite green, because both existing pair tests
    give the subject exactly one graded partner -- an over-determined fixture, the same
    shape PR #113's review found in the ORDER BY pin.

    UNFILTERED, THE COMMAND MISREPORTS TWICE OVER: it prints an unrelated drug's grade
    under a `subject -> partner` heading, and because `found` is then non-zero it also
    SUPPRESSES the "ABSENT RULE, not a safety finding" line -- so a pair drugref has
    never graded is answered with somebody else's contraindication.
    """
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000011400", object_code="N0000011500",
        severity="minor", subject_unii="TESTUNIIP1", partner_unii="TESTUNIIP2",
        subject_name="the subject drug", partner_name="the asked-about partner")
    # A SECOND graded partner of the SAME subject, filed under the same object class, so
    # one lookup returns both rows and only the filter can tell them apart.
    bystander = _a_moiety(conn, ingest_run_id, "TESTUNIIP3", "an unrelated bystander")
    object_class = conn.execute(
        "SELECT object_class_uuid FROM drugref.curated_interaction "
        "WHERE subject_moiety_uuid = %s", (subject,)).fetchone()[0]
    _file_member(conn, bystander, object_class, ingest_run_id)

    from drugref import curated_read
    assert {g.partner_moiety for g in curated_read.effective_grades_for(conn, subject)} \
        == {partner, bystander}, (
        "the premise: the unfiltered lookup returns BOTH partners, so an unfiltered "
        "pair form would print the bystander")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    out = capsys.readouterr().out
    assert str(partner) in out
    assert str(bystander) not in out, (
        "the pair form answers about THIS pair; another partner's grade printed under a "
        "`subject -> partner` heading is a grade attributed to a drug it was not "
        "written about")


def test_an_unrankable_severity_is_flagged_in_the_line_that_carries_it(
        conn, ingest_run_id, capsys):
    """ISSUE 116 REACHES THE READER, not only the operator -- the PR #119 review's
    finding that `severity_rank` had NO Python reader anywhere in `src/`.

    THE WHOLE ARGUMENT for keeping `severity_rank` NULLABLE beside `effective_rank` is
    that it is "the only evidence the schema is broken" -- and the one consumer this
    project shipped to read `GradedPair` printed `effective_rank` alone, discarding that
    evidence at the single place a human looks at it.

    WHY `rank 0` UNMARKED IS THE WRONG RENDERING, and it is not a formatting quibble. In
    every other numbering a reader has met, 0 means LEAST. Here it means drugref cannot
    rank this severity AND this row has outranked and DISCARDED a real grade for the same
    pair -- which on this path is a contraindication the reader will never see. Deferring
    that to `drugref status` puts the warning in front of the operator and withholds it
    from the person being under-warned, in the same process, off a record that already
    carries the answer.
    """
    conn.execute("ALTER TABLE drugref.curated_interaction "
                 "DROP CONSTRAINT curated_interaction_severity")
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000011600", object_code="N0000011700",
        severity="wibble", subject_unii="TESTUNIIP4", partner_unii="TESTUNIIP5",
        subject_name="the subject drug", partner_name="the partner drug")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    out = capsys.readouterr().out
    assert "wibble" in out, "the offending word is usually the whole diagnosis"
    assert "UNRANKABLE" in out, (
        "an unrankable severity must be LOUD in the line that carries it -- `rank 0` on "
        "its own reads as 'least concerning', the exact inversion issue 116 is about")
    assert "drugref status" in out, (
        "and the reader is pointed at the command that lists the broken rulings")


def test_a_healthy_grade_carries_no_unrankable_marker(conn, ingest_run_id, capsys):
    """THE CONTROL for the test above: the marker must key off the FAULT, not fire on
    every line. A banner on every row is a banner an operator learns to skip, which is
    the standing argument `cli_status.py` makes for the class-grain block's bare counts.
    """
    subject, partner = _a_graded_pair(
        conn, ingest_run_id, subject_code="N0000011800", object_code="N0000011900",
        severity="contraindicated", subject_unii="TESTUNIIP6",
        partner_unii="TESTUNIIP7", subject_name="the subject drug",
        partner_name="the partner drug")

    assert cli_interactions._handle_interactions(conn, _args(subject, partner)) == 0
    out = capsys.readouterr().out
    assert "rank 1" in out
    assert "UNRANKABLE" not in out


def test_two_drugs_with_no_rule_between_them_say_so(conn, ingest_run_id, capsys):
    """AND THE ANSWER IS NOT 'SAFE'. drugref publishes facts, never verdicts (the
    standing rule `accumulation.py` states for the same reason), and an absent rule is
    an absent rule -- not evidence of absence. The wording has to carry that or the
    command becomes a clinical claim drugref has not made."""
    one = _a_moiety(conn, ingest_run_id, "TESTUNIIK3", "one drug")
    other = _a_moiety(conn, ingest_run_id, "TESTUNIIK4", "another drug")

    assert cli_interactions._handle_interactions(conn, _args(one, other)) == 0
    out = capsys.readouterr().out.lower()
    assert "no curated grade" in out
    # THE POSITIVE CLAIMS, NAMED INDIVIDUALLY rather than screened by the substring
    # "safe" -- which the honest disclaimer ("not a safety finding") contains itself, so
    # the crude check failed the correct implementation. Screening for the assertion
    # drugref must not make is the thing actually worth testing.
    for verdict in ("is safe", "no interaction", "safe to co-prescribe"):
        assert verdict not in out, (
            f"drugref publishes facts, never verdicts, and {verdict!r} is a verdict: "
            "an absent rule is an absent rule, not evidence of absence")
    assert "not a safety finding" in out, (
        "and the absence must be labelled, or the empty answer reads as reassurance")


def test_a_drug_against_itself_is_a_usage_error_not_a_data_answer(conn, ingest_run_id,
                                                                  capsys):
    """`--with` NAMING THE SAME MOIETY, which until the PR #119 review printed a
    factual-sounding answer about a pair that cannot exist.

    Both grains exclude self-pairs by construction, so the two lookups returned nothing
    and the command concluded "drugref holds no curated grade for this pair in either
    direction" -- a statement about the DATA in answer to a question about the ARGUMENT
    LIST. Exit 2, because nothing was asked.
    """
    one = _a_moiety(conn, ingest_run_id, "TESTUNIIP8", "one drug")

    assert cli_interactions._handle_interactions(conn, _args(one, one)) == 2
    out = capsys.readouterr().out.lower()
    assert "same drug" in out
    assert "no curated grade" not in out, (
        "a self-pair must not borrow the vocabulary of an absent rule -- there is no "
        "pair here to hold a grade")


def test_the_pair_form_finds_a_class_grain_rule_too(conn, ingest_run_id, capsys):
    """BOTH GRAINS REACH THE COMMAND, because both are in the view it reads. A pair
    graded only by a class rule is the shape the whole 5c.2 class tier exists to
    produce, and a command that silently handled only the moiety grain would look
    correct on today's data -- `class_pair_contraindication` is empty on every database
    in existence -- and go wrong on the first slice that populates it."""
    _sc, _oc, subjects, objects = _a_graded_class_rule(
        conn, ingest_run_id, subject_code="N0000011000", object_code="N0000011100",
        subject_members=[("TESTUNIIK5", "subject-drug")],
        object_members=[("TESTUNIIK6", "partner-drug")],
        severity="major")

    assert cli_interactions._handle_interactions(conn, _args(objects[0], subjects[0])) == 0
    out = capsys.readouterr().out
    assert "class_rule" in out
    assert "major" in out


# ============================================================================
# 3. the parser -- the command is reachable the way a user reaches it
# ============================================================================


def test_the_parser_accepts_one_moiety_and_a_pair():
    """A HANDLER NOTHING ROUTES TO is the same half-feature as a view nothing reads,
    one layer up -- so the wiring is asserted, not assumed.

    THE HANDLER LIVES IN `cli_interactions.py`, not `cli.py`, following `cli_policy`,
    `cli_signing` and `cli_curate`: each owns its handlers and a `register(commands)`
    that cli.build_parser calls, so the global --dsn/--log-level flags and the single
    connect-and-dispatch path in cli.main keep serving every command. cli.py was 461
    lines before this round and CLAUDE.md rule 4 caps files at ~500.
    """
    one = cli.build_parser().parse_args(
        ["interactions", "00000000-0000-0000-0000-000000000001"])
    assert one.handler is cli_interactions._handle_interactions
    assert one.moiety == uuid.UUID(int=1)
    assert one.against is None

    pair = cli.build_parser().parse_args(
        ["interactions", "00000000-0000-0000-0000-000000000001",
         "--with", "00000000-0000-0000-0000-000000000002"])
    assert pair.against == uuid.UUID(int=2)


def test_a_uuid_that_is_not_one_is_rejected_by_the_parser():
    """argparse's own error, not a traceback from psycopg three layers down: the
    moiety is typed as `uuid.UUID`, so a malformed identifier fails at the boundary
    with the offending value named."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["interactions", "not-a-uuid"])


def _args(moiety, against=None):
    """The parsed-args shape `_handle_interactions` reads, built by the real parser so
    a test cannot drift from the flags a user actually types."""
    argv = ["interactions", str(moiety)]
    if against is not None:
        argv += ["--with", str(against)]
    return cli.build_parser().parse_args(argv)


def test_the_command_reads_the_view_only_through_curated_read():
    """THE PRECEDENCE RULE STAYS IN ONE PLACE: this module must not name the view.

    NARROWER THAN IT WAS, AND THAT IS THE FIX. The project-wide guard against embedded
    SQL over curated tables lives in `test_curation_orphans.py`, parses each module with
    `ast` rather than grepping raw source, and covers six tables -- and until the PR #119
    review `cli_interactions` was simply missing from its module list while THIS test
    stood in for it. It stood badly: its first assertion was
    `"SELECT" not in source.upper() or "curated_read" in source`, and the right disjunct
    is ALWAYS true, because `curated_read.effective_grades_for` is in that function's
    source. It could not fail whatever SQL the module embedded.

    `cli_interactions` is now in that list, so the general rule is enforced generally.
    What survives here is the one thing that guard does NOT check: naming
    `curated_ddi_pair` at all -- the point being not merely "no SQL" but that the choice
    between two grains grading one pair is made in the view, once.

    PARSED, NOT GREPPED, and the distinction is load-bearing here rather than stylistic:
    this module's own docstring NAMES `curated_ddi_pair_effective` while explaining why
    it must not query it, so a raw substring search over the source fails on the
    paragraph documenting the rule. `ast` separates the two -- docstrings are prose about
    the code, every other string literal is code -- which is the same reason
    `test_the_cli_embeds_no_sql_against_a_curated_table` parses rather than greps.

    NO FIXTURES: this reads source text, and DB-gating a pure inspection meant it was
    skipped on exactly the runs that skip the database.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cli_interactions))
    docstrings = {ast.get_docstring(node, clean=False) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))}
    code_strings = [node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value not in docstrings]
    assert code_strings, "the parse found no string literals at all -- it is not looking"
    offenders = [s for s in code_strings if "curated_ddi_pair" in s]
    assert offenders == [], (
        "read the view through curated_read, so the precedence rule stays in ONE place; "
        f"found {offenders}")


def test_interactions_is_listed_in_the_command_help(capsys):
    """DISCOVERABLE. A read surface a user cannot find is the third form of the same
    half-feature this whole issue is about."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    assert "interactions" in capsys.readouterr().out
