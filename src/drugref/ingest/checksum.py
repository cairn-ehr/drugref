"""One checksum over an ingest's input files, shared by every orchestrator.

WHAT IT IS FOR. `ingest_run.source_checksum` answers "which bytes did this run
actually read?" -- the question a coverage number is only meaningful against. Two
runs reporting different figures are either two releases or one bug, and without
this there is no way to tell which.

WHY IT IS ONE FUNCTION. It was four (#43): `run.py` and `medrt_run.py` hashed a
single path with `read_bytes()`, while `mesh_run.py` and `mesh_ci_run.py` carried
body-identical chunked multi-path copies, each re-explaining the chunking in its own
docstring. The single-path form is just this one with one argument -- and, since
SHA-256 does not care how its input was fed in, the digests are identical, so
collapsing them rewrote no provenance already on disk. Pinned by test.
"""
import hashlib
import pathlib

# Accepted as str or Path throughout the ingest layer, as every parser does.
# Restated rather than imported from a parser: this module must not depend on any
# particular feed, and one line is cheaper than that coupling.
StrPath = str | pathlib.Path

# 1 MiB. Large enough that the read syscalls are not the cost, small enough that
# peak memory does not depend on the release.
CHUNK_BYTES = 1 << 20


def checksum(*paths: StrPath) -> str:
    """SHA-256 over every path, in the order given, as ONE digest.

    ONE digest and not one per file, because what a run consumed is the TUPLE of
    files: a MeSH ingest reads desc and supp together, and its provenance has to
    change if either does. Order is therefore part of the answer -- each caller
    passes its files in a fixed order, so the same release always hashes the same
    and a different layout reads as a different release.

    READ IN CHUNKS, NOT read_bytes(), and this is load-bearing rather than tidy:
    supp2026 is ~750 MB uncompressed, and slurping it whole would spike peak memory
    far above the streaming parsers' measured 32.7 MB -- the checksum has no reason
    to undo the bounded-memory property the parsers were written for.
    """
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
                digest.update(chunk)
    return digest.hexdigest()
