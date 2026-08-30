# tests/test_ingest_checksum.py
"""One release-file checksum, shared by every orchestrator (#43) -- no database.

`_checksum` existed four times: two single-path copies using read_bytes(), and two
body-identical multi-path ones hashing in 1 MiB chunks, each re-explaining in its
own docstring why chunking is load-bearing. It is now one function, and the two
facts that made collapsing them safe are pinned here rather than asserted in a
commit message:

* chunking does not change the digest, so every source_checksum already recorded in
  an ingest_run stays valid and no provenance was rewritten by this refactor;
* the multi-file digest depends on the ORDER of its inputs, which is what makes it
  a checksum of a RELEASE (a fixed tuple of files) rather than of a bag of bytes.
"""
import hashlib
import pathlib
import ast
import re

import drugref.ingest
from drugref.ingest.checksum import CHUNK_BYTES, checksum

INGEST = pathlib.Path(drugref.ingest.__file__).resolve().parent


def test_a_single_file_hashes_exactly_as_read_bytes_would(tmp_path):
    """THE COMPATIBILITY PIN. run.py and medrt_run.py hashed with
    `sha256(path.read_bytes())`; both now call this. If the chunked form disagreed,
    every ingest_run row written before this refactor would carry a checksum no
    re-run could reproduce -- provenance silently broken, nothing failing.
    """
    path = tmp_path / "release.txt"
    path.write_bytes(b"a UNII release, in miniature")
    assert checksum(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_file_larger_than_one_chunk_still_matches(tmp_path):
    """The case chunking exists for, exercised across the boundary rather than
    assumed: supp2026 is ~750 MB and slurping it would undo the streaming parser's
    bounded memory (measured 32.7 MB peak, spec section F)."""
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * (CHUNK_BYTES * 2 + 7))
    assert checksum(path) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_several_files_hash_as_one_digest(tmp_path):
    """A MeSH run reads three files, and its provenance must change if ANY of them
    does -- so the three collapse to one digest rather than three columns."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"desc")
    second.write_bytes(b"supp")
    assert checksum(first, second) == hashlib.sha256(b"descsupp").hexdigest()


def test_the_order_of_the_files_is_part_of_the_digest(tmp_path):
    """Each orchestrator passes its files in a fixed order; swapping two of them is
    a different release layout and must read as a different checksum."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"desc")
    second.write_bytes(b"supp")
    assert checksum(first, second) != checksum(second, first)


def test_only_checksum_py_hashes_an_ingest_input():
    """THE SINGLE-PLACE PIN, the property the other three shared helpers each have and
    this one did not. #43 collapsed four copies into this module, but pbs_run.py kept
    hashing its own items.csv -- with a DIFFERENT API (`hashlib.file_digest`), which is
    how a duplicate survives a refactor that was looking for its own idiom.

    A grep over the ingest package rather than an import, exactly as
    test_provenance's one-writer contract greps: driving the expectation off the code
    under test would pass whatever that code said. Scoped to drugref/ingest because
    db.py legitimately hashes migration TEXT for the ledger -- a different question,
    with a different meaning, that this helper has no business answering.

    Matched on the IMPORT rather than on the word, because prose is not code: the
    comment recording what pbs_run.py used to do names `hashlib.file_digest`, and a
    substring grep would read that explanation as the defect it explains.
    """
    imports_hashlib = re.compile(r"^(?:import hashlib|from hashlib import)", re.M)
    hashers = [p for p in sorted(INGEST.rglob("*.py"))
               if imports_hashlib.search(p.read_text())]
    # spl.py is the ONE exemption, and it is the same exemption db.py already
    # has one directory up: it hashes CONTENT to mint an identity
    # (`section_key` -- the SHA-256 of a normalised section, which is
    # spl_wording's primary key and is pinned by a CHECK on its shape), not an
    # INPUT FILE to record what a run read. Those are different questions with
    # different meanings, and `checksum` has no business answering the first --
    # it opens paths and streams bytes.
    #
    # THE DISTINCTION IS WHAT THIS TEST IS ABOUT, so it is CHECKED rather than
    # asserted in a comment: spl.py may reach hashlib from `section_key` and from
    # nowhere else. `section_key` takes a `str` and returns the wording's
    # identity; it opens nothing. spl_run.py -- the orchestrator that DOES
    # checksum both corpora, all 19.3 GB of them -- calls `checksum` and appears
    # nowhere in this list, which is the property being pinned.
    #
    # Checked with `ast` rather than by grepping for `open(`, because spl.py is a
    # STREAMING PARSER and legitimately opens zip members to read records: "does
    # it open files" is the wrong question, "does it hash anywhere but
    # section_key" is the right one.
    assert [p.name for p in hashers] == ["checksum.py", "spl.py"]
    assert _functions_reaching_hashlib(INGEST / "spl.py") == {"section_key"}

    # AND NO MODULE MAY REACH hashlib WITHOUT SAYING SO AT THE TOP. A
    # function-local import evades the grep above completely; it is not a style
    # preference here, it is the hole this test fell through once.
    for path in sorted(INGEST.rglob("*.py")):
        for line in path.read_text().splitlines():
            if line.startswith((" ", "\t")) and line.strip().startswith(
                    ("import hashlib", "from hashlib import")):
                raise AssertionError(
                    f"{path.name} imports hashlib inside a function, which hides "
                    "it from this module's single-place pin")


def _functions_reaching_hashlib(path) -> set[str]:
    """The names of the top-level functions in `path` that name `hashlib`."""
    tree = ast.parse(path.read_text())
    reaching = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id == "hashlib":
                reaching.add(node.name)
    return reaching


def test_a_changed_byte_anywhere_changes_the_digest(tmp_path):
    """The whole point of recording it: a re-run against an altered release must be
    distinguishable from a re-run against the same one."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"desc")
    second.write_bytes(b"supp")
    before = checksum(first, second)
    second.write_bytes(b"suppX")
    assert checksum(first, second) != before
