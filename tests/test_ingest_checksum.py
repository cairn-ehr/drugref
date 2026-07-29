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

from drugref.ingest.checksum import CHUNK_BYTES, checksum


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


def test_a_changed_byte_anywhere_changes_the_digest(tmp_path):
    """The whole point of recording it: a re-run against an altered release must be
    distinguishable from a re-run against the same one."""
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_bytes(b"desc")
    second.write_bytes(b"supp")
    before = checksum(first, second)
    second.write_bytes(b"suppX")
    assert checksum(first, second) != before
