# tests/test_cli_drugcentral.py
"""`drugref ingest drugcentral`: a standalone subcommand, not a chain step.

FDA-CYP set that precedent and this follows it. The dump is 1.4 GB and pinned to
one 2023 release with no successor offered; the chain is the routine
rebuild-everything path, and a source that cannot refresh does not belong in it.
"""
import pytest

from drugref import cli, cli_drugcentral


def test_the_subcommand_is_registered():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["ingest", "drugcentral", "--dump", "d.sql.gz", "--release", "11012023"])
    assert str(args.dump) == "d.sql.gz"
    assert args.release == "11012023"
    # Object identity, not just "some handler is set": a wrong wire-up (e.g. the
    # generic _handle_ingest, or the fda-cyp handler copy-pasted) would still leave
    # `args.handler` truthy and the two asserts above would still pass, since
    # neither reads through the handler at all.
    assert args.handler is cli_drugcentral.handle_drugcentral


def test_the_release_is_required():
    """Unlike fda-cyp, the dump states no release of its own that drugref reads,
    so provenance depends on the operator naming it."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest", "drugcentral", "--dump", "d.sql.gz"])


def test_drugcentral_is_not_a_chain_step():
    """A chain step would resolve its inputs before ANY step runs, so a node
    without the 1.4 GB dump would see the whole chain abort -- the failure
    IngestStep.packaged_defaults was added for."""
    assert "drugcentral" not in {step.name for step in cli.STEPS}
