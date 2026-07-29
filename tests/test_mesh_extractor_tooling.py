# tests/test_mesh_extractor_tooling.py
"""The MeSH fixture extractors must read the release as NLM publishes it (#40).

`tests/fixtures/make_mesh_subset.py` is an operator tool, not shipped code, but a
documented regeneration command that finds nothing is exactly the kind of rot that
goes unnoticed until someone needs to regenerate a fixture -- which is the moment
they are least able to tell "this release changed" from "this script was wrong".
Two things are pinned here, both established from what NLM actually serves:

* the release files are named `pa2026.xml`, `desc2026.gz`, `supp2026.gz` -- so the
  gzipped two are NOT `<stem>.xml.gz`, and a hardcoded ".xml" finds neither;
* both are read through a gz-aware streamer, as slice 5b's extractor already was.

The extractors stay stdlib-only and importable without drugref on the path (every
one of the five is, deliberately -- see test_medrt_parser's redaction test for why
an extractor must be checkable independently of the code it feeds), so this module
loads the script by path rather than importing a package.
"""
import gzip
import importlib.util
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load(script_name: str):
    """Import a fixture-extractor script by path (it is not on any import path)."""
    path = FIXTURES / script_name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def maker():
    return _load("make_mesh_subset.py")


def test_release_file_is_found_under_the_name_nlm_publishes(tmp_path, maker):
    """`desc2026.gz`, not `desc2026.xml` and not `desc2026.xml.gz`.

    This is the concrete shape of the documented command's failure: a downloads
    directory holding a genuine release, and a script looking for a file name the
    release does not use.
    """
    (tmp_path / "pa2026.xml").write_bytes(b"<PharmacologicalActionSet/>")
    (tmp_path / "desc2026.gz").write_bytes(gzip.compress(b"<DescriptorRecordSet/>"))

    assert maker._release_file(tmp_path, "pa2026").name == "pa2026.xml"
    assert maker._release_file(tmp_path, "desc2026").name == "desc2026.gz"


def test_a_missing_release_file_is_reported_not_guessed(tmp_path, maker):
    """Every candidate name is listed in the error. A regeneration that cannot find
    its input must say which names it looked for -- silently writing a fixture from
    an empty read would delete test cases nobody would notice were gone."""
    with pytest.raises(SystemExit) as excinfo:
        maker._release_file(tmp_path, "supp2026")
    assert "supp2026" in str(excinfo.value)


def test_the_extractor_streams_a_gzipped_release_file(tmp_path, maker):
    """The reader half: slice 5b's extractor already handled `.gz` and slice 2b's
    did not, so which fixture needed a manual 750 MB gunzip was a coin toss."""
    path = tmp_path / "desc2026.gz"
    path.write_bytes(gzip.compress(
        b"<DescriptorRecordSet>"
        b"<DescriptorRecord><DescriptorUI>D000001</DescriptorUI></DescriptorRecord>"
        b"</DescriptorRecordSet>"))
    uis = [rec.findtext("DescriptorUI")
           for rec in maker._iter(path, "DescriptorRecord")]
    assert uis == ["D000001"]
