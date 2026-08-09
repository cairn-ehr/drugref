# Slice 5c.4 — Signing the Curated Overlay: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give drugref's append-only curated overlay two layers of cryptographic attestation — per-row curator
signatures under curator-held Ed25519 keys, and per-release institutional content manifests — over one mechanism,
one canonical payload format, and one verification path.

**Architecture:** Signatures are **detached**: they live in their own insert-only table and point at the row they
cover, rather than sitting in a column on it. That is what lets one mechanism carry both layers, lets a row be
signed at any time after it exists, and lets several curators counter-sign one judgement. A **pure** module
(`signing.py`) owns the canonical byte format, the Ed25519 primitives and the verdict rule; three thin DB modules
(`keys.py`, `signatures.py`, `releases.py`) own the tables; `cli_signing.py` is the operator surface.

**Tech Stack:** Python 3.12 · `uv` · `psycopg` v3 · PostgreSQL ≥ 18 · `cryptography` (PyCA) for Ed25519 · pytest ·
ruff.

**Spec:** [`docs/superpowers/specs/2026-08-09-drugref-slice-5c4-signing-design.md`](../specs/2026-08-09-drugref-slice-5c4-signing-design.md).
Section references below (§3, §4.5, …) are to that file. **If this plan disagrees with the spec, the spec wins** —
and fix the plan, because a plan is a claim about the code and correcting it only in the code leaves it wrong.

## Global Constraints

Every task's requirements implicitly include this section.

- **TDD, without exception.** Write the failing test, run it and *see it fail for the stated reason*, then
  implement. A test that has never been observed failing is not evidence.
- **All tests must pass before every commit.** `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test
  user=postgres' uv run pytest`. The DB-gated majority *skips* without that DSN, so a green run without it means
  nothing. **Baseline at branch start: 969 passed.**
- **Lint before every commit:** `uv run ruff check .` — must be clean. `line-length = 88` is enforced on `src/`;
  `tests/**` is carved out of E501 (issue 79).
- **`src/` files stay under ~500 lines.** `cli.py` is at 508 and Task 1 exists to fix that before anything is added.
- **Inline documentation is mandatory and is written for a junior contributor.** Match the density of the
  surrounding modules — every non-obvious decision carries the argument for it, not just a restatement of the code.
- **No vocabulary is restated in Python.** Status values, target kinds and algorithm names live in `db/030`'s tables
  and CHECKs. No argparse `choices` over them; an error message quotes `db.constraint_definition` rather than
  listing values. The one exception is the frozen payload field lists (§4.5), which are frozen *on purpose* and
  carry their own catalog alarm.
- **Nothing in `keys.py` / `signatures.py` / `releases.py` commits.** The caller owns the transaction, as in every
  other module here. `cli_signing.py` commits, as every other CLI handler does.
- **`cli_signing.py` writes no SQL of its own**, like `cli.py` and `cli_policy.py`.
- **`db/030` is unmerged until this branch merges, so it may be edited across tasks** (PROJECT-NOTES: the ledger
  binds a database, not the repo). The suite's `_migrated` fixture drops and re-applies the schema each session, so
  edits are picked up. **Once merged it is frozen** — corrections then need `db/031`.
- **Issue-tracker hygiene:** in commit messages, keep any issue number away from `close`/`fix`/`resolve` in any
  inflection. Write "issue 82", not "#82", near those words. GitHub's linker matches token adjacency, not meaning.
- **Commit after every task**, with a message explaining *why*, not just what.

## File Structure

| File | Responsibility |
|---|---|
| `src/drugref/cli_chain.py` *(create)* | The **pure** chain-planning layer, extracted: `IngestStep`, the `ChainError`/`InputResolutionError`/`ReleaseError` family, `_release_flag`, `resolve_inputs`, `selected_steps`, `check_release_agreement`. ~150 lines. **Imports nothing from `drugref`**, which is what makes an import cycle structurally impossible. |
| `src/drugref/cli.py` *(modify)* | Keeps the seven `_run_*` wrappers, `STEPS`, the four `_handle_*` entry points, `_Parser`, `build_parser` and `main`; imports `cli_chain`. Drops to ~360 lines. |
| `src/drugref/signing.py` *(create)* | **PURE.** No DB, no filesystem. Canonical payload encoder, value rendering, frozen field lists, Ed25519 keygen/sign/verify, fingerprint, digest, and the verdict rule. ~260 lines. |
| `src/drugref/keys.py` *(create)* | The `signing_key` registry: register, revoke (via `overlay.supersede`), read. ~150 lines. |
| `src/drugref/signatures.py` *(create)* | Build a target row's canonical payload from the database; record a signature; verify one target. ~200 lines. |
| `src/drugref/releases.py` *(create)* | Build, publish and verify a release manifest. ~220 lines. |
| `src/drugref/cli_signing.py` *(create)* | `drugref keys generate\|register\|revoke\|list`, `sign`, `verify`, `publish`. ~280 lines. |
| `db/030_signing.sql` *(create)* | Six tables, two seeded vocabularies, `forbid_any_rewrite`, the re-issued read views, `curated_signature_status`, `signature_backdated`. |
| `tests/fixtures/signing_vectors.json` *(create)* | Committed canonical-format test vectors. |
| `tests/make_signing_vectors.py` *(create)* | The re-runnable generator for that fixture, following `make_*_subset.py` precedent. |
| `tests/test_signing_primitives.py`, `test_signing_payload.py`, `test_signing_verdict.py`, `test_signing_schema.py`, `test_keys_writer.py`, `test_signatures_writer.py`, `test_releases.py`, `test_cli_signing.py`, `test_signature_read_path.py` *(create)* | One test module per unit. |

**Task-to-spec mapping:** Task 1 → §10.1 · Task 2 → §4.6, §11 · Task 3 → §4.1–4.5 · Task 4 → §7.1 · Task 5 → §5 ·
Task 6 → §5.1, §6, §10.2 · Task 7 → §4.4, §7.1, §10.3 · Task 8 → §5.5, §7.2, §8 · Task 9 → §7.3, §9 · Task 10 →
§12 measurement + wrap-up.

---

### Task 1: Extract `cli.py`'s pure chain-planning layer into `cli_chain.py`

**Why first:** `cli.py` is 508 lines, already over CLAUDE.md's ~500 cap, and PROJECT-NOTES states the remedy as a
prerequisite — *"Splitting it is the next change to that file, before any new handler."* This slice adds seven
handlers. **This task changes no behaviour**; the existing suite is the entire gate.

**WHICH SIDE OF THE SEAM MOVES, and why it is this one.** PROJECT-NOTES describes the seam as the four `_handle_*`
entry points (which take a connection) versus the DB-free argument layer above them. Extracting *either* side
honours that seam — but extracting the **handlers** cannot work, and this was measured rather than reasoned:
`STEPS` eagerly references the `_run_*` wrappers, so `cli` must import whatever module holds them, while
`_handle_chain` calls `selected_steps`, `resolve_inputs` and `check_release_agreement`, so that module must import
`cli`. The imports are mutual, and Python raises `AttributeError: partially initialized module … has no attribute
'run_unii'` the moment anything imports the handler module first — which the signing tests would.

Extracting the **pure** layer has no such hazard, because it depends on nothing in `drugref` at all. That property
is what makes the cycle structurally impossible rather than merely absent today, and Step 1 pins it.

**Files:**
- Create: `src/drugref/cli_chain.py`
- Modify: `src/drugref/cli.py`
- Test: `tests/test_cli.py` (existing — two new tests, plus any reference that must follow a moved name)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `cli_chain.IngestStep`, `cli_chain.ChainError`, `cli_chain.InputResolutionError`,
  `cli_chain.ReleaseError`, `cli_chain._release_flag`, `cli_chain.resolve_inputs`, `cli_chain.selected_steps`,
  `cli_chain.check_release_agreement`. `cli.py` imports the names it uses, so `cli.STEPS`, `cli.resolve_inputs`,
  `cli.check_release_agreement`, `cli.IngestStep`, `cli.ChainError`, `cli.build_parser` and `cli.main` all keep
  working unchanged. The four `_handle_*` entry points and the seven `_run_*` wrappers **stay in `cli.py`** and
  keep their current names.
- **One signature changes: `selected_steps(args)` → `selected_steps(args, steps)`.** It is the only planning
  function that reads a module global (`for step in STEPS`, cli.py:261), and a function's free variables resolve
  against the namespace of the module it is *defined* in — so moving its source to `cli_chain.py`, where `STEPS`
  does not exist, turns that into a `NameError`. **The parameter is required, with no default**, and the fix is
  the shape its own sibling already has: `check_release_agreement(plan)` takes its data explicitly rather than
  reaching for a global, so `selected_steps` was the odd one out and moving it merely exposed that. Five call
  sites update — `_handle_chain` passes `STEPS`, and four in `tests/test_cli.py` pass `cli.STEPS`. A default
  would defeat the point: an implicit table is exactly the hidden state being removed, and a wrong-or-empty
  default fails silently, which this file's own docstrings warn against three separate times.

- [ ] **Step 1: Write the failing test that pins the split**

Add to `tests/test_cli.py`:

```python
def test_cli_chain_imports_nothing_from_drugref():
    """THE PROPERTY THAT MAKES THE IMPORT CYCLE IMPOSSIBLE, and the reason this split
    runs in this direction rather than the other.

    The first attempt at this task moved the HANDLERS out instead, and could not work.
    `STEPS` eagerly references the `_run_*` wrappers, so cli must import whatever module
    holds them; `_handle_chain` calls selected_steps/resolve_inputs/
    check_release_agreement, so that module must import cli. Mutual -- and Python raises
    `AttributeError: partially initialized module ... has no attribute 'run_unii'` as
    soon as anything imports the handler module first, which the signing tests would.

    Extracting the pure layer has no such hazard because it depends on nothing in
    drugref. This test is what keeps that true: cli_chain must never grow a drugref
    import, and if it ever needs one, the layering is wrong rather than the test.
    """
    import ast
    import inspect
    from drugref import cli_chain

    tree = ast.parse(inspect.getsource(cli_chain))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    from_drugref = {m for m in imported if m == "drugref" or m.startswith("drugref.")}
    assert from_drugref == set(), (
        f"cli_chain imports {sorted(from_drugref)} from drugref. It must import "
        "nothing from drugref -- that is what makes the cycle structurally impossible "
        "rather than merely absent today.")


def test_cli_py_is_under_the_size_cap():
    """CLAUDE.md rule 4, measured rather than assumed. 500 is the stated cap."""
    import pathlib
    from drugref import cli
    lines = len(pathlib.Path(cli.__file__).read_text().splitlines())
    assert lines <= 500, f"cli.py is {lines} lines, over the ~500 cap"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_cli.py -k "cli_chain or size_cap" -v`

Expected: the first FAILS with `ModuleNotFoundError: No module named 'drugref.cli_chain'`, the second FAILS
reporting 508 lines.

- [ ] **Step 3: Create `cli_chain.py`**

Move, **verbatim**, from `cli.py`: `IngestStep` (including its `__post_init__`), `ChainError`,
`InputResolutionError`, `ReleaseError`, `_release_flag`, `resolve_inputs`, `selected_steps` and
`check_release_agreement`. Keep every docstring and comment **word-for-word** — they carry arguments that took
review rounds to get right (why both zero and several glob matches are errors, why `selected_steps` tests
presence rather than truthiness, why the `secondary` exemption filters the claim and never the read). A reflow
here would be an unreviewed rewrite.

**The single exception to "verbatim" is `selected_steps`' signature** (see Interfaces): it becomes
`selected_steps(args, steps)` and iterates the parameter. Add a short paragraph to its docstring saying why the
table is passed rather than read:

```
THE STEP TABLE IS A PARAMETER, not a module global, and that is what let this function
move here at all: a function's free variables resolve against the namespace of the
module it is DEFINED in, so reading `STEPS` from a file that does not define it is a
NameError. It is also the shape its sibling already had -- check_release_agreement takes
its plan explicitly -- so the global read was the anomaly. Required, with no default: an
implicit table is the hidden state being removed here, and a wrong or empty default
would select nothing while reporting success, which is the failure this module's own
docstrings warn against three times over.
```

Head the new file:

```python
# src/drugref/cli_chain.py
"""Chain planning: everything that can settle an invocation BEFORE a database exists.

EXTRACTED FROM cli.py in slice 5c.4, along the seam PROJECT-NOTES named -- the DB-free
argument layer versus the handlers that take a connection. cli.py was 508 lines, over
CLAUDE.md's ~500 cap, and the signing slice adds seven handlers, so the split had to
happen before them rather than after.

THIS SIDE MOVED, NOT THE HANDLERS, and the reason is structural rather than aesthetic.
cli.STEPS eagerly references the `_run_*` wrappers, so cli must import whatever module
holds them; `_handle_chain` calls the three functions below, so that module would have
to import cli. The imports are mutual, and Python raises AttributeError on a
partially-initialised module the moment the handler module is imported first.

THIS MODULE IMPORTS NOTHING FROM drugref, which is what makes that cycle impossible
rather than merely absent -- pinned by test_cli_chain_imports_nothing_from_drugref. If
a future change appears to need a drugref import here, the layering is wrong, not the
test.

DETERMINISTIC BUT NOT FILESYSTEM-FREE: `resolve_inputs` globs the downloads tree, so
its tests want a tmp_path and nothing more.
"""
```

`cli.py` keeps its module docstring, but its fourth paragraph — *"THAT LAYER IS NOT
'EVERYTHING ABOVE `main`'…"* — describes a layer that has now left the file. Replace that paragraph with:

```
THAT ARGUMENT LAYER NOW LIVES IN cli_chain.py, extracted in slice 5c.4 -- the step
table's type, the ChainError family, `resolve_inputs`, `selected_steps` and
`check_release_agreement`. What remains here takes a connection or builds the parser:
the `_run_*` wrappers, the four `_handle_*` entry points, `_Parser`, `build_parser` and
`main`. The extraction ran in that direction because cli_chain can import nothing from
drugref, which is what makes an import cycle structurally impossible; moving the
handlers out instead creates one, since STEPS references the runners while
`_handle_chain` needs the planning functions.
```

- [ ] **Step 4: Rewire `cli.py`**

Import from `cli_chain` exactly the names `cli.py` still uses — no more, or ruff's `F401` will fail the lint gate:

```python
from drugref.cli_chain import (ChainError, IngestStep, check_release_agreement,
                               resolve_inputs, selected_steps)
```

`STEPS`, `_handle_chain`, `_Parser`, `build_parser` and `main` are otherwise untouched.

**`InputResolutionError` and `ReleaseError` are raised in `cli_chain` and caught nowhere in `cli.py`** (`main`
catches the `ChainError` base), so importing them here would be unused. `tests/test_cli.py` references
`cli.InputResolutionError` in four places — repoint those at `cli_chain.InputResolutionError` rather than adding
a `noqa`. Report in your task report which test references you moved and why.

- [ ] **Step 5: Confirm the no-SQL grep still covers everything it should**

`tests/test_curation_orphans.py`'s `test_the_cli_embeds_no_sql_against_a_curated_table` parses `cli.py` and
`cli_policy.py`. **Under this split it needs no change** — the handlers stayed in `cli.py`, so no candidate query
moved anywhere. Read the test and confirm that is actually so rather than assuming it: the guard exists because a
Python-embedded writer to an append-only curated table is invisible to the `pg_rewrite` sweep that finds every
other reader, and a split that silently moved a query out from under it would be exactly the kind of gap this
project keeps finding. Say in your report which files the guard covers and that you verified no query left them.

- [ ] **Step 6: Run the full suite**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q`
Expected: **971 passed** (969 baseline + the 2 new tests). Any other number means the move changed behaviour.

- [ ] **Step 7: Lint**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/drugref/cli.py src/drugref/cli_chain.py tests/test_cli.py
git commit -m "refactor: extract cli.py's pure chain-planning layer into cli_chain.py

cli.py was 508 lines, over CLAUDE.md's ~500 cap, and PROJECT-NOTES recorded
the split as a prerequisite for the next handler rather than a cleanup. The
signing slice adds seven.

THE SEAM IS THE ONE PROJECT-NOTES NAMED -- handlers versus the DB-free
argument layer -- but the extraction runs in the other direction, and that
was measured rather than chosen. Moving the HANDLERS out cannot work: STEPS
eagerly references the _run_* wrappers, so cli must import the handler
module, while _handle_chain calls selected_steps/resolve_inputs/
check_release_agreement, so the handler module must import cli. Mutual, and
Python raises AttributeError on a partially-initialised module as soon as
anything imports the handler module first -- which the signing tests would.

The pure layer has no such hazard because it imports nothing from drugref,
and test_cli_chain_imports_nothing_from_drugref keeps that structural rather
than incidental.

No behaviour change -- docstrings and comments moved word-for-word, because
they carry arguments (why zero AND several glob matches are both errors, why
selected_steps tests presence not truthiness, why `secondary` filters the
claim and never the read) that cost review rounds.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add `cryptography` and the Ed25519 primitives

**Files:**
- Modify: `pyproject.toml`
- Create: `src/drugref/signing.py`
- Test: `tests/test_signing_primitives.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `signing.ED25519: str` (`"Ed25519"`) · `signing.generate_keypair() -> tuple[bytes, bytes]` returning
  `(private_32, public_32)` · `signing.fingerprint(public_key: bytes) -> str` (64 lowercase hex) ·
  `signing.sign(private_key: bytes, payload: bytes) -> bytes` (64 bytes) ·
  `signing.verify(public_key: bytes, payload: bytes, signature: bytes) -> bool` ·
  `signing.digest(payload: bytes) -> bytes` (32 bytes).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signing_primitives.py`:

```python
# tests/test_signing_primitives.py
"""The Ed25519 primitives. PURE -- no database, so these run everywhere."""
import hashlib

import pytest

from drugref import signing


def test_a_generated_keypair_has_the_expected_raw_sizes():
    private, public = signing.generate_keypair()
    assert len(private) == 32
    assert len(public) == 32


def test_a_signature_verifies_against_its_own_payload():
    private, public = signing.generate_keypair()
    signature = signing.sign(private, b"a canonical payload")
    assert len(signature) == 64
    assert signing.verify(public, b"a canonical payload", signature) is True


def test_a_tampered_payload_does_not_verify():
    """The whole point of the layer, in one assertion: one flipped byte breaks it."""
    private, public = signing.generate_keypair()
    signature = signing.sign(private, b"severity=major")
    assert signing.verify(public, b"severity=minor", signature) is False


def test_another_key_does_not_verify():
    private, _ = signing.generate_keypair()
    _, other_public = signing.generate_keypair()
    signature = signing.sign(private, b"payload")
    assert signing.verify(other_public, b"payload", signature) is False


def test_verify_returns_false_rather_than_raising_on_a_malformed_signature():
    """A verifier that RAISES on rubbish is a verifier every caller must wrap, and one
    that a caller will eventually wrap too widely. Garbage in the signature column is
    an ordinary thing to find in a table an attacker can INSERT into -- it is a `false`,
    not an exception."""
    _, public = signing.generate_keypair()
    assert signing.verify(public, b"payload", b"not a signature at all") is False


def test_verify_returns_false_rather_than_raising_on_a_malformed_public_key():
    """Same argument one column over: signing_key.public_key is bytea, and a 31-byte
    value is a row somebody can write."""
    private, _ = signing.generate_keypair()
    signature = signing.sign(private, b"payload")
    assert signing.verify(b"\x00" * 31, b"payload", signature) is False


def test_signing_is_deterministic():
    """Ed25519 derives its nonce from the key and message, so there is no per-signature
    randomness to get wrong -- the failure mode that leaks an ECDSA private key. Pinned
    because it is a property this project relies on when comparing signatures."""
    private, _ = signing.generate_keypair()
    assert signing.sign(private, b"x") == signing.sign(private, b"x")


def test_the_fingerprint_is_sha256_over_the_raw_public_key():
    """Stated here as an INDEPENDENT computation rather than by calling the function
    twice: the fingerprint is the identity a signature names, so a change to how it is
    derived orphans every signature ever recorded."""
    _, public = signing.generate_keypair()
    assert signing.fingerprint(public) == hashlib.sha256(public).hexdigest()
    assert len(signing.fingerprint(public)) == 64


def test_the_digest_is_sha256_of_the_payload():
    assert signing.digest(b"abc") == hashlib.sha256(b"abc").digest()


def test_generate_keypair_does_not_repeat_itself():
    assert signing.generate_keypair()[0] != signing.generate_keypair()[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_signing_primitives.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'drugref.signing'`.

- [ ] **Step 3: Add the dependency, with the rule-6 determination recorded**

In `pyproject.toml`, change the `dependencies` line and add the comment above it:

```toml
# RULE 6 (CLAUDE.md): every dependency must be AGPL-3.0-compatible, CHECKED BEFORE
# ADDING. `cryptography` (PyCA) declares the license expression
# "Apache-2.0 OR BSD-3-Clause" (PyPI metadata, verified against 50.0.0, which ships
# both LICENSE.APACHE and LICENSE.BSD). Dual licensing lets drugref ELECT
# BSD-3-Clause, which is AGPL-3.0-compatible outright -- so the clearance does not
# even rest on Apache-2.0's one-way compatibility with GPLv3/AGPLv3.
#
# It is unavoidable rather than convenient: there is no Ed25519 in the Python standard
# library, and a pure-Python implementation of a signature scheme is exactly the thing
# not to hand-roll. NOTICE is unchanged -- it attributes bundled reference-data
# SOURCES, and this redistributes no data.
dependencies = ["psycopg[binary]>=3.2", "cryptography>=42"]
```

Run: `uv sync`

- [ ] **Step 4: Write `signing.py`'s primitive half**

```python
# src/drugref/signing.py
"""The signing subsystem's PURE half: bytes in, bytes out, no database (slice 5c.4).

WHAT LIVES HERE AND WHY IT IS SEPARATE. Three things: the Ed25519 primitives, the
CANONICAL PAYLOAD FORMAT (the artefact everything else rests on), and the verdict rule
that says what a signature means. None of them touches a connection, so all of them are
testable without one -- which matters most for the canonical format, whose entire job is
to be reproducible from a stored row years from now.

THE VERDICT RULE IS HERE, NOT IN A DB MODULE, on accumulation.fires' precedent: drugref
publishes facts rather than verdicts and hands out the rules as code, so "why did this
verify?" has one answer everywhere rather than one per caller.

ALGORITHM: Ed25519. 32-byte keys, 64-byte signatures, no parameter choices to get
wrong, and deterministic -- the nonce is derived from key and message, so there is no
per-signature randomness and therefore no RNG failure mode of the kind that leaks an
ECDSA private key. The name is stored per key and per signature (db/030) so a second
algorithm is an additive migration rather than a rewrite.
"""
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

# The one Python spelling of the algorithm name. db/030's CHECK is its vocabulary home;
# this constant exists because Python must write the value into a row, not as a second
# list to disagree with the first -- an unrecognised value raises CheckViolation from
# the database, which is the intended behaviour.
ED25519 = "Ed25519"


def generate_keypair() -> tuple[bytes, bytes]:
    """A fresh keypair as (private_32, public_32) RAW bytes.

    Raw rather than PEM or DER deliberately: the private half is written to a file the
    curator holds and the public half into a bytea column, and both want the smallest
    unambiguous encoding. A container format would add a parser -- and a second way for
    two 32-byte keys to compare unequal.
    """
    private = Ed25519PrivateKey.generate()
    return (private.private_bytes_raw(), private.public_key().public_bytes_raw())


def fingerprint(public_key: bytes) -> str:
    """SHA-256 over the raw public key, lowercase hex. THE IDENTITY A SIGNATURE NAMES.

    Changing this derivation orphans every signature ever recorded, because
    assertion_signature.key_fingerprint is how a signature finds its key. It is pinned
    by a test that recomputes it independently rather than by calling this function.
    """
    return hashlib.sha256(public_key).hexdigest()


def digest(payload: bytes) -> bytes:
    """SHA-256 of the canonical payload -- what a manifest entry stores.

    The manifest records digests rather than whole payloads so that a manifest over
    thousands of rows stays a manifest. Verification recomputes the payload from the
    live row and re-digests it, so the digest is a comparison key, never the thing
    signed: Ed25519 signs the payload itself (see `sign`).
    """
    return hashlib.sha256(payload).digest()


def sign(private_key: bytes, payload: bytes) -> bytes:
    """A 64-byte Ed25519 signature over the payload ITSELF, not over its digest.

    Ed25519 hashes internally, so pre-hashing would be both redundant and a different
    scheme (Ed25519ph) that a third-party verifier following this project's published
    format would not reproduce.
    """
    return Ed25519PrivateKey.from_private_bytes(private_key).sign(payload)


def verify(public_key: bytes, payload: bytes, signature: bytes) -> bool:
    """True if `signature` is a valid Ed25519 signature by `public_key` over `payload`.

    RETURNS FALSE RATHER THAN RAISING on malformed input -- a wrong-length key, a
    truncated signature, rubbish in either column. Both values come out of a table an
    attacker can INSERT into, so garbage there is an ordinary finding rather than an
    exceptional one, and a verifier that raises is one every caller must wrap -- and one
    a caller will eventually wrap too widely, swallowing a real error beside it.
    """
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (InvalidSignature, ValueError):
        return False
    return True
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_signing_primitives.py -v`
Expected: 10 passed.

- [ ] **Step 6: Run the full suite and lint**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q && uv run ruff check .`
Expected: **981 passed**, lint clean.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/drugref/signing.py tests/test_signing_primitives.py
git commit -m "feat: Ed25519 primitives for the signing subsystem

Rule 6 cleared BEFORE adding, as a blocker rather than a cleanup item:
cryptography (PyCA) declares 'Apache-2.0 OR BSD-3-Clause', so drugref
elects BSD-3-Clause and the clearance does not even rest on Apache-2.0's
one-way GPLv3 compatibility. There is no stdlib Ed25519 and hand-rolling a
signature scheme is not on the table, so the dependency is unavoidable.
NOTICE unchanged: it attributes bundled data sources, not code deps.

verify() returns False rather than raising on a malformed key or signature.
Both values live in a table an attacker can INSERT into, so garbage is an
ordinary finding; a raising verifier is one every caller must wrap, and one
a caller eventually wraps too widely.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The canonical payload — encoder, value rendering, frozen field lists, test vectors

**This is the load-bearing task.** If the format is wrong, every signature this slice produces is worthless.

**Files:**
- Modify: `src/drugref/signing.py`
- Create: `tests/test_signing_payload.py`, `tests/make_signing_vectors.py`, `tests/fixtures/signing_vectors.json`
- Test: `tests/test_signing_payload.py`

**Interfaces:**
- Consumes: `signing.digest` (Task 2).
- Produces: `signing.PROLOGUE: bytes` · `signing.render(value) -> str | None` ·
  `signing.canonical_payload(context: str, fields: Sequence[tuple[str, str | None]], groups: Sequence[tuple[str,
  Sequence[Sequence[tuple[str, str | None]]]]] = ()) -> bytes` ·
  `signing.CURATED_INTERACTION_V1: tuple[str, ...]` · `signing.CURATED_CONDITION_V1: tuple[str, ...]` ·
  `signing.RELEASE_MANIFEST_V1: tuple[str, ...]` · `signing.FIELD_LISTS: dict[str, tuple[str, ...]]` ·
  `signing.ATTESTATION_FIELDS: tuple[str, str]` = `("signer_key_fingerprint", "signed_at")`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signing_payload.py`:

```python
# tests/test_signing_payload.py
"""The canonical payload format (spec 4.1-4.5). PURE -- no database.

EVERY TEST HERE IS A PROPERTY OF THE FORMAT ITSELF, not of drugref's tables. The format
has to be reproducible from a stored row years from now and reimplementable in another
language from the spec, so what is pinned is the bytes -- not that two calls agree with
each other, which any broken encoder also manages.
"""
import datetime as dt
import json
import pathlib

import pytest

from drugref import signing

VECTORS = pathlib.Path(__file__).parent / "fixtures" / "signing_vectors.json"


def test_the_prologue_and_context_open_the_payload():
    payload = signing.canonical_payload("curated_condition/v1", (("x", "1"),))
    assert payload.startswith(b"drugref-sig-v1\ncurated_condition/v1\n1\n")


def test_the_field_count_is_stated():
    payload = signing.canonical_payload("t/v1", (("a", "1"), ("b", "2")))
    assert payload.split(b"\n")[2] == b"2"


def test_null_and_the_empty_string_are_different_bytes():
    """`mechanism IS NULL` means 'no mechanism recorded' and `mechanism = ''` means a
    curator wrote an empty one. 5c.1 already rests on that distinction elsewhere --
    a NULL question_uuid MEANS 'this grade rests on nothing recorded'. A format that
    folded them would let either be substituted for the other under a valid signature.
    """
    assert (signing.canonical_payload("t/v1", (("mechanism", None),))
            != signing.canonical_payload("t/v1", (("mechanism", ""),)))


def test_a_null_field_is_tagged_N_with_zero_length():
    payload = signing.canonical_payload("t/v1", (("mechanism", None),))
    assert b"9:mechanism:N:0:\n" in payload


def test_a_value_containing_a_newline_is_length_delimited_not_line_delimited():
    """Length prefixes rather than delimiters is the whole reason this format is not
    CSV-shaped: `management` is free text a curator writes, and a newline in it must not
    be able to forge a field boundary."""
    payload = signing.canonical_payload("t/v1", (("mechanism", "a\nb"),))
    assert b"9:mechanism:S:3:a\nb\n" in payload


def test_a_value_that_imitates_the_encoding_cannot_forge_a_field():
    """The adversarial case the length prefix exists for: a curator (or an attacker
    writing an unsigned row) puts something shaped like an encoded field INSIDE a value.
    Two payloads that would collide under a delimiter-scanning parser must differ."""
    a = signing.canonical_payload("t/v1", (("x", "1:y:S:1:z"), ("y", "")))
    b = signing.canonical_payload("t/v1", (("x", ""), ("y", "1:y:S:1:z")))
    assert a != b


def test_lengths_are_utf8_BYTE_counts_not_character_counts():
    """A character count would disagree between a Python implementation and one in a
    language counting UTF-16 code units, which is exactly the interoperability the
    format exists to provide."""
    payload = signing.canonical_payload("t/v1", (("x", "é"),))
    assert b"1:x:S:2:\xc3\xa9\n" in payload


def test_the_context_separates_domains():
    """Spec 4.4: a condition ruling's bytes must never verify as an interaction
    judgement. The context line is what makes that structural rather than hoped for."""
    fields = (("subject_moiety_uuid", "3f7a1c22-0b64-5e9d-9a11-8c4f2e6b0d13"),)
    assert (signing.canonical_payload("curated_interaction/v1", fields)
            != signing.canonical_payload("curated_condition/v1", fields))


def test_group_members_are_sorted_so_row_order_cannot_change_the_payload():
    """A manifest is built from a SELECT, and a SELECT without ORDER BY may return rows
    in any order. If member order reached the bytes, the same database would publish two
    different manifests."""
    one = (("target_id", "1"),)
    two = (("target_id", "2"),)
    assert (signing.canonical_payload("t/v1", (), (("entries", [one, two]),))
            == signing.canonical_payload("t/v1", (), (("entries", [two, one]),)))


def test_a_group_header_names_the_group():
    payload = signing.canonical_payload("t/v1", (), (("entries", [(("id", "1"),)]),))
    assert b"--entries--\n" in payload


def test_two_groups_stay_distinct():
    """Members must not migrate between groups without changing the bytes."""
    m = (("id", "1"),)
    assert (signing.canonical_payload("t/v1", (), (("entries", [m]), ("upstream", [])))
            != signing.canonical_payload("t/v1", (), (("entries", []), ("upstream", [m]))))


# ---- value rendering -------------------------------------------------------


def test_a_timestamp_renders_as_utc_with_exactly_six_fractional_digits():
    """Six digits always, including when the microseconds are zero: a variable-length
    rendering means the same instant has two spellings, and only one of them verifies."""
    aest = dt.timezone(dt.timedelta(hours=10))
    assert (signing.render(dt.datetime(2026, 8, 9, 14, 31, 7, 123456, tzinfo=aest))
            == "2026-08-09T04:31:07.123456Z")
    assert (signing.render(dt.datetime(2026, 8, 9, 4, 31, 7, 0, tzinfo=dt.timezone.utc))
            == "2026-08-09T04:31:07.000000Z")


def test_a_naive_timestamp_is_refused():
    """psycopg returns timestamptz as aware, so a naive value means somebody built it in
    Python -- and rendering it would silently assume a zone. Refuse rather than guess."""
    with pytest.raises(ValueError, match="naive"):
        signing.render(dt.datetime(2026, 8, 9))


def test_a_boolean_renders_before_the_integer_branch():
    """isinstance(True, int) is True in Python, so a bool tested after int renders as
    '1' -- which is also how the integer 1 renders. Two different values, one spelling."""
    assert signing.render(True) == "true"
    assert signing.render(False) == "false"
    assert signing.render(1) == "1"


def test_a_uuid_renders_lowercase_canonical():
    import uuid
    value = uuid.UUID("3F7A1C22-0B64-5E9D-9A11-8C4F2E6B0D13")
    assert signing.render(value) == "3f7a1c22-0b64-5e9d-9a11-8c4f2e6b0d13"


def test_bytes_render_as_lowercase_hex():
    assert signing.render(b"\xde\xad\xbe\xef") == "deadbeef"


def test_none_renders_as_none_not_as_a_string():
    assert signing.render(None) is None


def test_an_unrenderable_type_is_refused_rather_than_stringified():
    """str() on anything is what makes a format silently wrong: a Decimal, a memoryview
    or a dict would each get SOME spelling, and none of them a specified one."""
    with pytest.raises(TypeError):
        signing.render({"a": 1})


def test_text_is_not_unicode_normalised():
    """The signature commits to the bytes Postgres stored, not to a normalised shadow of
    them. NFC-normalising here would make two distinct stored strings sign identically."""
    decomposed = "é"          # e + combining acute
    composed = "é"             # precomposed e-acute
    assert signing.render(decomposed) != signing.render(composed)


# ---- the frozen field lists ------------------------------------------------


def test_every_field_list_ends_with_the_attestation_pair():
    """Spec 4.4: the signer and the moment are INSIDE the signed bytes, so a signature
    cannot be re-attributed to another key by editing a column, nor walked across a
    revocation boundary by editing a timestamp."""
    for context, fields in signing.FIELD_LISTS.items():
        assert fields[-2:] == signing.ATTESTATION_FIELDS, context


def test_no_field_list_names_a_surrogate_key_or_superseded_by():
    """target_id is a POINTER, not content. GENERATED ALWAYS AS IDENTITY values are
    local to one database, so signing them would break a signature carried into another;
    superseded_by is the one column the floor lets change, so signing it would invalidate
    every signature the moment its row is corrected."""
    for context, fields in signing.FIELD_LISTS.items():
        assert "superseded_by" not in fields, context
        assert not any(f.endswith("_id") for f in fields), context


def test_the_field_lists_have_no_duplicates():
    for context, fields in signing.FIELD_LISTS.items():
        assert len(fields) == len(set(fields)), context


# ---- the published test vectors --------------------------------------------


def test_the_committed_vectors_reproduce():
    """REGRESSION DETECTION, and it is worth being exact about what this does and does
    not prove. The vectors are GENERATED by the same encoder they check, so they cannot
    establish that the format is correct -- only that it has not DRIFTED. Correctness
    rests on the property tests above plus review of the format itself.

    What the fixture adds beyond that is independent checkability: it stores the payload
    bytes as an escaped literal beside their digest, so a reviewer can read the payload
    by eye and confirm the digest with `sha256sum` without running any drugref code --
    which is also what a third party reimplementing the format needs.
    """
    vectors = json.loads(VECTORS.read_text())
    assert vectors["format"] == "drugref-sig-v1"
    for case in vectors["cases"]:
        fields = [(name, value) for name, value in case["fields"]]
        groups = [(g["name"], [[(n, v) for n, v in m] for m in g["members"]])
                  for g in case.get("groups", [])]
        payload = signing.canonical_payload(case["context"], fields, groups)
        assert payload == case["payload"].encode("utf-8"), case["name"]
        assert signing.digest(payload).hex() == case["digest"], case["name"]


def test_the_vector_signatures_verify_under_the_committed_test_key():
    """The test key is 32 bytes of 00..1f -- obviously a test key, never registered
    anywhere real. Ed25519 is deterministic, so a signature is reproducible and can be
    committed."""
    vectors = json.loads(VECTORS.read_text())
    private = bytes.fromhex(vectors["test_private_key"])
    public = bytes.fromhex(vectors["test_public_key"])
    assert signing.fingerprint(public) == vectors["test_key_fingerprint"]
    for case in vectors["cases"]:
        payload = case["payload"].encode("utf-8")
        assert signing.sign(private, payload).hex() == case["signature"], case["name"]
        assert signing.verify(public, payload, bytes.fromhex(case["signature"]))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_signing_payload.py -v`
Expected: all FAIL — `AttributeError: module 'drugref.signing' has no attribute 'canonical_payload'`, and the two
vector tests with `FileNotFoundError`.

- [ ] **Step 3: Implement the encoder in `signing.py`**

Append to `src/drugref/signing.py`:

```python
# ---- the canonical payload (spec 4.1-4.5) ----------------------------------
#
# THE FORMAT, in full, so it is reimplementable from this comment alone:
#
#     drugref-sig-v1\n
#     <context>\n                                       * ^[a-z_]+/v[0-9]+$, validated
#     <field-count>\n
#     <len(name)>:<name>:<tag>:<len(value)>:<value>\n   * field-count, FROZEN order
#     --<len(group)>:<group>:<member-count>--\n         * zero or more groups
#     <member-field-count>\n                            * one block per member; members
#     <len(name)>:<name>:<tag>:<len(value)>:<value>\n     sorted by their own COMPLETE
#                                                        encoding, count line included
#
# `tag` is S for a present value or N for SQL NULL (length 0, empty value). Lengths are
# UTF-8 BYTE counts. The trailing newlines are readability only -- the lengths and counts
# are what delimit, which is exactly why a newline inside a value cannot forge a boundary.
#
# EVERY STRUCTURAL LINE IS SELF-DELIMITING, and that was a correction. The first draft
# applied the length-prefix principle to VALUES but not to the format's own structure,
# and three collisions followed -- each demonstrated against the shipped encoder:
#
#   g=[{a:1,b:9},{a:2,b:8}] == g=[{a:1},{b:9,a:2,b:8}]   no per-member field count
#   g=[{a:1},{b:2}]         == g=[{a:1,b:2}]             no member count
#   group named "x--\n--y"  == two empty groups          group name not length-prefixed
#   context "evil/v1\n99"                                context forged the count line
#
# No forgery followed in this codebase -- member arity is fixed by the code building each
# group, and contexts are constants -- but a canonical format whose canonicity depends on
# its callers behaving is not canonical, and this is a published reference third parties
# implement against. Fixed while three test vectors existed and nothing had been signed;
# after the first real signature the format can never change again.
#
# THE PAYLOAD IS GENERATE-AND-COMPARE. IT IS NEVER PARSED, by drugref or by anyone.
# Verification re-derives the bytes from the stored row and compares. The format is
# documented so a third party can REPRODUCE the bytes from their own copy of the data,
# which is all a verifier needs -- not so anyone can write a parser and then depend on
# guarantees a generator does not owe them.
#
# WHY NOT JSON/RFC 8785. JCS is a published standard and its genuinely hard part is
# NUMBER canonicalisation, which this format sidesteps entirely by rendering every value
# as a string. At that point JCS contributes JSON's familiarity and an escaping surface
# to implement wrong. What it would have bought -- independent checkability -- is bought
# instead by tests/fixtures/signing_vectors.json, which stores each payload beside its
# digest so both can be checked without running drugref.
PROLOGUE = b"drugref-sig-v1"


def render(value) -> str | None:
    """One Python value -> its canonical string form (or None for SQL NULL).

    EVERY VALUE BECOMES A STRING, which is what removes number canonicalisation -- the
    part of RFC 8785 that is hard to reimplement correctly -- from the problem entirely.

    BOOL IS TESTED BEFORE INT ON PURPOSE: isinstance(True, int) is True in Python, so
    the other order renders True as '1', which is also how the integer 1 renders. Two
    different values, one spelling, under a valid signature.

    An unrecognised type RAISES rather than falling back to str(). A fallback is what
    makes a format silently wrong: a Decimal, a memoryview or a dict would each get SOME
    spelling and none of them a specified one, so a second implementation would disagree.

    TEXT IS NOT NORMALISED. The signature commits to the bytes Postgres stored; NFC here
    would make two distinct stored strings sign identically.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, uuid.UUID):
        return str(value)                      # lowercase canonical 8-4-4-4-12
    if isinstance(value, dt.datetime):
        return _render_timestamp(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    raise TypeError(
        f"{type(value).__name__} has no canonical rendering. Add one deliberately -- a "
        "str() fallback would give it a spelling nothing specifies, and a second "
        "implementation of this format would choose differently.")


def _render_timestamp(value: dt.datetime) -> str:
    """RFC 3339, UTC, EXACTLY six fractional digits.

    Six always, including when the microseconds are zero: a variable-length rendering
    gives one instant two spellings and only one of them verifies. Postgres timestamptz
    has microsecond resolution, so six is lossless.

    A NAIVE datetime RAISES. psycopg returns timestamptz as aware, so a naive value means
    somebody constructed it in Python, and rendering it would silently assume a zone --
    which would produce a valid signature over the wrong instant.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "a naive datetime has no canonical rendering: it names no instant, and "
            "assuming a zone would sign the wrong one")
    utc = value.astimezone(dt.timezone.utc)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond:06d}Z"


def _encode_field(name: str, value: str | None) -> bytes:
    """One `<len>:<name>:<tag>:<len>:<value>\\n` record."""
    name_b = name.encode("utf-8")
    if value is None:
        return b"%d:%s:N:0:\n" % (len(name_b), name_b)
    value_b = value.encode("utf-8")
    return b"%d:%s:S:%d:%s\n" % (len(name_b), name_b, len(value_b), value_b)


_CONTEXT = re.compile(r"^[a-z_]+/v[0-9]+$")

Field = tuple[str, str | None]
Group = tuple[str, Sequence[Sequence[Field]]]


def canonical_payload(context: str,
                      fields: Sequence[Field] = (),
                      groups: Sequence[Group] = ()) -> bytes:
    """The bytes a signature is made over. THE LOAD-BEARING ARTEFACT OF THIS SLICE.

    `context` is the domain separator (spec 4.4) -- `curated_interaction/v1`,
    `curated_condition/v1`, `release_manifest/v1`. It is inside the payload, so bytes
    signed as one kind of statement can never verify as another.

    `fields` is a sequence of (name, rendered-value) pairs IN THE FROZEN ORDER for that
    context. Order is part of the format: two orderings of one row are two different
    payloads, which is why FIELD_LISTS is a frozen tuple and not a dict.

    `groups` is a sequence of (group_name, members), each member itself a field-pair
    sequence. MEMBERS ARE SORTED BY THEIR OWN ENCODING, because a manifest is built from
    a SELECT and a SELECT without ORDER BY may return rows in any order -- if that order
    reached the bytes, one database would publish two different manifests. Sorting the
    ENCODED member (rather than by some key) means the rule needs no knowledge of what a
    member contains.
    """
    if not _CONTEXT.match(context):
        raise ValueError(
            f"{context!r} is not a valid context. It occupies a whole line of the "
            "payload, so a newline in it forges the field-count line below; the "
            "pattern is deliberately narrower than 'anything without a newline'.")
    out = [PROLOGUE, b"\n", context.encode("utf-8"), b"\n",
           str(len(fields)).encode("ascii"), b"\n"]
    out.extend(_encode_field(name, value) for name, value in fields)
    for group_name, members in groups:
        name_b = group_name.encode("utf-8")
        out.append(b"--%d:%s:%d--\n" % (len(name_b), name_b, len(members)))
        # Each member carries its OWN field count, and members sort by their complete
        # encoding -- that count line included. Without the count two members ran
        # together into one; without sorting on the complete encoding a SELECT's row
        # order could reach the bytes.
        out.extend(sorted(
            b"%d\n%s" % (len(member),
                         b"".join(_encode_field(n, v) for n, v in member))
            for member in members))
    return b"".join(out)


# ---- the frozen field lists (spec 4.5) -------------------------------------
#
# FROZEN CONSTANTS, AND THIS DELIBERATELY INVERTS A STANDING RULE. The gates round's
# rule reads "derive the covered set from the catalog, never from a list you maintain",
# and here the opposite is required: deriving the payload from information_schema means
# a later ALTER TABLE ADD COLUMN silently changes every payload and INVALIDATES EVERY
# SIGNATURE EVER MADE.
#
# The alarm the rule exists for is rebuilt rather than abandoned --
# tests/test_signing_payload_coverage.py compares these lists against the live catalog
# and FAILS on a new column, forcing an explicit choice: bump to /v2, or exclude the
# column with a stated reason. Frozen bytes, catalog-driven alarm.
#
# Adding a field to a list below without bumping the context is a BREAKING change to
# every signature already recorded. There is no way to make that safe; there is only a
# test that makes it deliberate.
ATTESTATION_FIELDS = ("signer_key_fingerprint", "signed_at")

CURATED_INTERACTION_V1 = (
    "subject_moiety_uuid", "object_class_uuid", "relationship", "applies",
    "severity", "mechanism", "management", "evidence_grade", "question_uuid",
    "source", "reviewed_by", "reviewed_against", "reviewed_at",
    *ATTESTATION_FIELDS)

# NOTE THE ASYMMETRY, which mirrors db/029's own and is not an oversight: this table is
# keyed on the (drug, condition) PAIR and carries `ruling` where its sibling carries
# `relationship` + `applies`, because one pair genuinely holds both an indication and a
# contraindication in 168 cases. See spec 3 of the 5c.1 design.
CURATED_CONDITION_V1 = (
    "subject_moiety_uuid", "object_condition_uuid", "ruling",
    "severity", "mechanism", "management", "evidence_grade", "question_uuid",
    "source", "reviewed_by", "reviewed_against", "reviewed_at",
    *ATTESTATION_FIELDS)

# The manifest's scalars. The two group cardinalities are stated as scalars as well as
# being derivable from the groups themselves (spec 5.5): a group truncated at its END is
# otherwise detectable only by recomputing the whole digest, and a scalar count makes
# that specific failure nameable.
RELEASE_MANIFEST_V1 = (
    "release_tag", "published_by", "published_at", "entry_count", "upstream_count",
    *ATTESTATION_FIELDS)

FIELD_LISTS = {
    "curated_interaction/v1": CURATED_INTERACTION_V1,
    "curated_condition/v1": CURATED_CONDITION_V1,
    "release_manifest/v1": RELEASE_MANIFEST_V1,
}
```

Add `import datetime as dt` and `import uuid` to the module's imports.

- [ ] **Step 4: Write the vector generator and generate the fixture**

Create `tests/make_signing_vectors.py`:

```python
# tests/make_signing_vectors.py
"""Regenerate tests/fixtures/signing_vectors.json.

RE-RUNNABLE AND COMMITTED, following this repo's make_*_subset.py precedent: a fixture
nobody can regenerate is a fixture nobody can check.

WHAT THESE VECTORS ARE FOR, stated precisely because it is easy to over-read. They are
generated by the encoder they check, so they CANNOT establish that the format is
correct -- only that it has not drifted. Correctness rests on the property tests in
tests/test_signing_payload.py plus review of the format itself.

What they add is INDEPENDENT CHECKABILITY: each case stores the payload as an escaped
JSON string beside its digest, so a reviewer can read the payload by eye and confirm the
digest with `sha256sum` without running any drugref code. That is also exactly what a
third party reimplementing the format in another language needs.

THE TEST KEY IS 32 BYTES OF 00..1f -- obviously not a real key, and never registered in
any database. Ed25519 is deterministic, so its signatures are reproducible and committable.

Run:  uv run python -m tests.make_signing_vectors > tests/fixtures/signing_vectors.json
"""
import json

from drugref import signing

TEST_PRIVATE = bytes(range(32))

CASES = [
    {
        "name": "an applying interaction judgement, fully graded",
        "context": "curated_interaction/v1",
        "fields": [
            ["subject_moiety_uuid", "3f7a1c22-0b64-5e9d-9a11-8c4f2e6b0d13"],
            ["object_class_uuid", "c1d9e04a-7b23-5f18-8e6c-2a90d4f7b155"],
            ["relationship", "CI_PE"],
            ["applies", "true"],
            ["severity", "major"],
            ["mechanism", "additive anticoagulant effect"],
            ["management", "monitor INR; consider dose reduction"],
            ["evidence_grade", "established"],
            ["question_uuid", None],
            ["source", "DRUGREF"],
            ["reviewed_by", "a curator"],
            ["reviewed_against", "MED-RT 2026.07.06"],
            ["reviewed_at", "2026-08-09T04:31:07.123456Z"],
            ["signer_key_fingerprint",
             signing.fingerprint(signing.generate_keypair()[1])],
            ["signed_at", "2026-08-09T04:33:52.008117Z"],
        ],
    },
]


def build() -> dict:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    public = Ed25519PrivateKey.from_private_bytes(TEST_PRIVATE).public_key(
        ).public_bytes_raw()
    fp = signing.fingerprint(public)
    cases = []
    for case in _cases(fp):
        fields = [(n, v) for n, v in case["fields"]]
        groups = [(g["name"], [[(n, v) for n, v in m] for m in g["members"]])
                  for g in case.get("groups", [])]
        payload = signing.canonical_payload(case["context"], fields, groups)
        cases.append({**case,
                      "payload": payload.decode("utf-8"),
                      "digest": signing.digest(payload).hex(),
                      "signature": signing.sign(TEST_PRIVATE, payload).hex()})
    return {"format": "drugref-sig-v1",
            "note": ("Generated by tests/make_signing_vectors.py. These detect DRIFT, "
                     "not incorrectness -- the generator is the encoder under test. "
                     "`payload` is checkable by eye and `digest` with sha256sum."),
            "test_private_key": TEST_PRIVATE.hex(),
            "test_public_key": public.hex(),
            "test_key_fingerprint": fp,
            "cases": cases}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
```

`_cases(fp)` returns three cases, each exercising something the property tests cannot show together:
1. **the fully-graded interaction judgement** above, with `signer_key_fingerprint` set to `fp` and
   `question_uuid` NULL — proves the NULL tag in a realistic row;
2. **a retiring condition ruling** — `ruling = "spurious"`, `severity`/`evidence_grade`/`mechanism`/`management`
   all NULL, and a `mechanism` value in a *different* case containing a newline and a colon;
3. **a release manifest with two entries and two upstream releases** — the only case exercising groups, with the
   members supplied in *reverse* sorted order in the fixture source so the committed payload proves the sort ran.

Run: `uv run python -m tests.make_signing_vectors > tests/fixtures/signing_vectors.json`

- [ ] **Step 5: Write the catalog alarm test**

Create `tests/test_signing_payload_coverage.py`:

```python
# tests/test_signing_payload_coverage.py
"""The alarm that lets the frozen field lists stay frozen (spec 4.5). DB-gated."""
import pytest

from drugref import signing

CURATED = [
    ("curated_interaction/v1", "curated_interaction", "curated_interaction_id"),
    ("curated_condition/v1", "curated_condition", "curated_condition_id"),
]


@pytest.mark.parametrize("context,table,pk", CURATED)
def test_the_frozen_field_list_accounts_for_every_column(conn, context, table, pk):
    """A new column on a curated table must FAIL here rather than drift into the void.

    THIS IS THE INVERSE OF THE STANDING RULE ON PURPOSE. Everywhere else in this suite,
    a covered set is derived from the catalog so a new object is covered the day it
    lands. A signed payload cannot work that way: derive it from information_schema and
    an ALTER TABLE ADD COLUMN silently changes every payload and invalidates every
    signature ever made. So the list is frozen and THIS test is the alarm -- a new
    column fails, forcing a deliberate choice (bump the context to /v2, or exclude the
    column here with a stated reason) instead of a silent one.

    The two excluded columns are excluded for different reasons, and neither is
    incidental. The surrogate primary key is a POINTER, local to one database, so
    signing it would break a signature carried into another. `superseded_by` is the ONE
    column db/020's floor permits to change, so signing it would invalidate every
    signature the moment its row was corrected -- which is to say, every time the
    overlay did the thing it exists to do.
    """
    live = {row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'drugref' AND table_name = %s", (table,)).fetchall()}
    signed = set(signing.FIELD_LISTS[context]) - set(signing.ATTESTATION_FIELDS)
    deliberately_unsigned = {pk, "superseded_by"}
    assert live == signed | deliberately_unsigned, (
        f"drugref.{table}'s columns and {context}'s frozen field list disagree. "
        f"Only in the table: {sorted(live - signed - deliberately_unsigned)}. "
        f"Only in the field list: {sorted(signed - live)}. "
        "A new column is a DELIBERATE decision: sign it under a new /v2 context, or "
        "add it to deliberately_unsigned with the reason. Do not add it to the v1 "
        "list -- that invalidates every signature already recorded.")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_signing_payload.py -v` → all pass.
Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_signing_payload_coverage.py -v` → 2 passed.

- [ ] **Step 7: Verify the vectors are independently checkable**

Run, and confirm the digest matches the fixture's first case by hand:

```bash
uv run python -c "
import json,pathlib
v=json.loads(pathlib.Path('tests/fixtures/signing_vectors.json').read_text())
print(v['cases'][0]['payload'], end='')" | shasum -a 256
```

Expected: the same hex as `cases[0].digest`. **This is the step that proves the vectors mean something** — it
computes the digest with a tool that has never seen drugref's code.

- [ ] **Step 8: Full suite, lint, commit**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q
uv run ruff check .
git add src/drugref/signing.py tests/test_signing_payload.py \
        tests/test_signing_payload_coverage.py tests/make_signing_vectors.py \
        tests/fixtures/signing_vectors.json
git commit -m "feat: the canonical payload format, frozen field lists and test vectors

The load-bearing artefact of slice 5c.4: if this is wrong, every signature
the slice produces is worthless. Length-prefixed rather than JSON, because
RFC 8785's hard part is number canonicalisation and rendering every value as
a string removes it entirely -- leaving JCS contributing familiarity plus an
escaping surface to implement wrong.

NULL and '' are different bytes, deliberately: a NULL mechanism means 'none
recorded' and '' means a curator wrote an empty one, and 5c.1 already rests
on that distinction for question_uuid.

The frozen field lists INVERT the standing 'derive from the catalog' rule,
and the alarm is rebuilt rather than dropped: deriving a signed payload from
information_schema means ALTER TABLE ADD COLUMN silently invalidates every
signature ever made, so the list is frozen and a new column FAILS
test_the_frozen_field_list_accounts_for_every_column instead.

The committed vectors detect DRIFT, not incorrectness -- they are generated
by the encoder they check. What they add is independent checkability: the
payload is readable by eye and its digest confirmable with sha256sum.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The verdict rule (pure)

**Files:**
- Modify: `src/drugref/signing.py`
- Test: `tests/test_signing_verdict.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `signing.KeyStatus` (frozen dataclass: `status: str`, `is_revocation: bool`,
  `invalidates_all_signatures: bool`, `status_from: datetime`) · the six verdict constants
  `NO_SIGNATURE`, `UNKNOWN_KEY`, `BAD_SIGNATURE`, `KEY_REVOKED_COMPROMISED`, `KEY_EXPIRED`, `VALID` ·
  `signing.verdict(key_status: KeyStatus | None, *, signature_ok: bool, signed_at: datetime) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signing_verdict.py`:

```python
# tests/test_signing_verdict.py
"""The verdict rule (spec 7.1). PURE -- no database.

ONE TEST PER BOUNDARY, per the standing rule slice 5c.1's PR review produced. The
precedence is the part that is easy to get subtly wrong, and every ordering mistake
produces a plausible-looking verdict rather than an error.
"""
import datetime as dt

from drugref import signing

EARLY = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
REVOKED_AT = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
LATE = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)

ACTIVE = signing.KeyStatus("active", is_revocation=False,
                           invalidates_all_signatures=False, status_from=EARLY)
ROTATED = signing.KeyStatus("rotated", is_revocation=True,
                            invalidates_all_signatures=False, status_from=REVOKED_AT)
COMPROMISED = signing.KeyStatus("compromised", is_revocation=True,
                                invalidates_all_signatures=True,
                                status_from=REVOKED_AT)


def test_a_good_signature_by_an_active_key_is_valid():
    assert signing.verdict(ACTIVE, signature_ok=True, signed_at=LATE) == signing.VALID


def test_an_active_key_does_not_expire_its_own_signatures():
    """THE REASON `is_revocation` EXISTS, and the test that kills its removal.

    An active key's status_from is its REGISTRATION time, and every signature it makes
    is necessarily after that. So a rule that expired any signature at or after
    status_from would expire EVERY signature ever made -- the layer would report
    key_expired universally, and nobody would notice until a consumer asked why nothing
    was ever valid.

    The alternative to the column is a Python-side `status == 'active'` test, which puts
    a member of db/030's vocabulary in a second place. Four rounds of this project have
    paid for that mistake already.
    """
    assert signing.verdict(ACTIVE, signature_ok=True,
                           signed_at=LATE) != signing.KEY_EXPIRED


def test_an_unknown_key_is_not_reported_as_a_bad_signature():
    """You cannot check the mathematics without the public key, so 'unknown' outranks
    'bad'. Conflating them reports a routine registry gap -- a key nobody has registered
    yet -- as an attack, which is the wrong alarm to raise at 3am."""
    assert signing.verdict(None, signature_ok=False,
                           signed_at=LATE) == signing.UNKNOWN_KEY
    assert signing.verdict(None, signature_ok=True,
                           signed_at=LATE) == signing.UNKNOWN_KEY


def test_a_bad_signature_outranks_a_revoked_key():
    """A forged signature under a revoked key is a forgery first. Reporting it as
    'revoked' would file an attack as a key-management event."""
    assert signing.verdict(COMPROMISED, signature_ok=False,
                           signed_at=EARLY) == signing.BAD_SIGNATURE


def test_a_compromised_key_invalidates_a_signature_made_long_before_the_revocation():
    """BLANKET, and that is the whole content of invalidates_all_signatures: after a
    compromise you cannot tell which signatures were the curator's and which the
    attacker's, so signed_at proves nothing."""
    assert signing.verdict(COMPROMISED, signature_ok=True,
                           signed_at=EARLY) == signing.KEY_REVOKED_COMPROMISED


def test_a_rotated_key_leaves_an_earlier_signature_valid():
    """TIME-SCOPED. A curator changing laptop must not unsign years of sound work; that
    is the case blanket-only revocation gets wrong, and it is the common one."""
    assert signing.verdict(ROTATED, signature_ok=True,
                           signed_at=EARLY) == signing.VALID


def test_a_rotated_key_expires_a_signature_made_after_the_rotation():
    assert signing.verdict(ROTATED, signature_ok=True,
                           signed_at=LATE) == signing.KEY_EXPIRED


def test_the_revocation_boundary_is_inclusive():
    """A signature made AT the revocation instant is expired, not valid. Either choice
    is defensible; the point is that one of them is written down, because an unstated
    boundary is where two implementations of this rule diverge."""
    assert signing.verdict(ROTATED, signature_ok=True,
                           signed_at=REVOKED_AT) == signing.KEY_EXPIRED


def test_no_signature_is_not_produced_by_the_rule_itself():
    """NO_SIGNATURE is the CALLER's verdict -- there is no signature to pass in. It
    lives in this module so all six spellings have one home, not because verdict()
    returns it."""
    assert signing.NO_SIGNATURE == "no_signature"


def test_every_verdict_constant_is_distinct():
    values = [signing.NO_SIGNATURE, signing.UNKNOWN_KEY, signing.BAD_SIGNATURE,
              signing.KEY_REVOKED_COMPROMISED, signing.KEY_EXPIRED, signing.VALID]
    assert len(set(values)) == 6
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_signing_verdict.py -v`
Expected: FAIL — `AttributeError: module 'drugref.signing' has no attribute 'KeyStatus'`.

- [ ] **Step 3: Implement**

Append to `src/drugref/signing.py` (and add `from dataclasses import dataclass` to the imports):

```python
# ---- what a signature MEANS (spec 7.1) -------------------------------------
#
# SIX VERDICTS, NOT A BOOLEAN, and the reason is the revocation model. A consumer needs
# to tell "this was forged" from "the curator's laptop was stolen last year", because
# the first is an attack and the second is a re-review queue. Collapsing them into
# pass/fail throws away the only information that decides what to do next.
NO_SIGNATURE = "no_signature"
UNKNOWN_KEY = "unknown_key"
BAD_SIGNATURE = "bad_signature"
KEY_REVOKED_COMPROMISED = "key_revoked_compromised"
KEY_EXPIRED = "key_expired"
VALID = "valid"


@dataclass(frozen=True)
class KeyStatus:
    """What the registry currently says about the key a signature names.

    Assembled by keys.py from signing_key's LIVE row joined to
    signing_key_status_kind. The two booleans arrive as DATA rather than being derived
    from `status` here, because `status` is db/030's vocabulary and a Python-side test
    against one of its members is the second-home defect this project has paid for four
    times over.
    """
    status: str
    is_revocation: bool
    invalidates_all_signatures: bool
    status_from: dt.datetime


def verdict(key_status: KeyStatus | None, *, signature_ok: bool,
            signed_at: dt.datetime) -> str:
    """What one signature is worth. PURE, and the ONE place the precedence lives.

    ORDER IS LOAD-BEARING, and each step outranks the next for a stated reason:

    1. UNKNOWN_KEY -- without the public key the mathematics cannot be checked at all,
       so `signature_ok` is not evidence of anything here. Reporting this as
       BAD_SIGNATURE files a registry gap as an attack.
    2. BAD_SIGNATURE -- a forgery is a forgery first. Reporting a forged signature under
       a revoked key as "revoked" would file an attack as a key-management event.
    3. KEY_REVOKED_COMPROMISED -- blanket, ignoring signed_at, because after a
       compromise you cannot tell the curator's signatures from the attacker's.
    4. KEY_EXPIRED -- time-scoped: the key was rotated or retired and this signature is
       at or after that boundary. `is_revocation` is what makes this an END boundary; an
       active key's status_from is its registration time, so without that guard every
       signature ever made would land here.
    5. VALID.

    NO_SIGNATURE is not returned here -- with no signature there is nothing to pass in.
    The caller reports it; the constant lives beside its siblings so the six spellings
    have one home.
    """
    if key_status is None:
        return UNKNOWN_KEY
    if not signature_ok:
        return BAD_SIGNATURE
    if key_status.invalidates_all_signatures:
        return KEY_REVOKED_COMPROMISED
    if key_status.is_revocation and signed_at >= key_status.status_from:
        return KEY_EXPIRED
    return VALID
```

- [ ] **Step 4: Run to verify passing, then the full suite and lint**

Run: `uv run pytest tests/test_signing_verdict.py -v` → 10 passed.
Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q && uv run ruff check .`

- [ ] **Step 5: Mutation-verify the `is_revocation` guard**

Temporarily delete `key_status.is_revocation and` from the `KEY_EXPIRED` branch, then run
`uv run pytest tests/test_signing_verdict.py -v`.

**Expected: `test_an_active_key_does_not_expire_its_own_signatures` FAILS.** Revert.

If it passes, that test is not pinning what it claims and must be fixed **before** moving on. This project has
found six load-bearing clauses no test killed the removal of; this one would be silent in production and would
report every signature as expired.

- [ ] **Step 6: Commit**

```bash
git add src/drugref/signing.py tests/test_signing_verdict.py
git commit -m "feat: the verdict rule -- six verdicts, one declared precedence

Six rather than a boolean, because a consumer must tell 'this was forged'
from 'the curator's laptop was stolen last year': the first is an attack,
the second a re-review queue, and pass/fail throws away the only fact that
decides what to do next.

Precedence is load-bearing. UNKNOWN_KEY outranks BAD_SIGNATURE because the
mathematics cannot be checked without the key at all, so signature_ok is not
evidence there -- conflating them files a registry gap as an attack.
BAD_SIGNATURE outranks revocation because a forgery under a revoked key is a
forgery first.

is_revocation is mutation-verified: without it, an active key's status_from
(its registration time) would expire every signature ever made, universally
and silently.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `db/030` — six tables, two seeded vocabularies, the insert-only floor

**Files:**
- Create: `db/030_signing.sql`
- Test: `tests/test_signing_schema.py`

**Interfaces:**
- Consumes: `drugref.forbid_overlay_rewrite` (db/020) and `drugref.forbid_multiple_live_assertions` (db/023),
  **reused unchanged — no new PL/pgSQL for `signing_key`**.
- Produces: tables `signing_key_status_kind`, `signature_target_kind`, `signing_key`, `assertion_signature`,
  `release_manifest`, `release_manifest_entry`; function `drugref.forbid_any_rewrite()`; index
  `signing_key_live_key`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signing_schema.py`. Helpers first:

```python
# tests/test_signing_schema.py
"""db/030's floor and vocabularies (spec 5). DB-gated."""
import datetime as dt

import psycopg
import pytest

from drugref import signing

FP = "a" * 64
OTHER_FP = "b" * 64
NOW = dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc)


def _key(conn, fingerprint=FP, status="active", status_from=NOW):
    return conn.execute(
        "INSERT INTO drugref.signing_key (key_fingerprint, public_key, algorithm, "
        "holder, status, status_from, registered_by) "
        "VALUES (%s, %s, 'Ed25519', 'a curator', %s, %s, 'an operator') "
        "RETURNING signing_key_id",
        (fingerprint, b"\x01" * 32, status, status_from)).fetchone()[0]


def _signature(conn, target_id=1, kind="curated_interaction", digest=b"\x02" * 32):
    return conn.execute(
        "INSERT INTO drugref.assertion_signature (target_kind, target_id, "
        "payload_context, payload_digest, key_fingerprint, algorithm, signature, "
        "signed_at) VALUES (%s, %s, 'curated_interaction/v1', %s, %s, 'Ed25519', %s, %s)"
        " RETURNING signature_id",
        (kind, target_id, digest, FP, b"\x03" * 64, NOW)).fetchone()[0]
```

Then the tests, each with the reason it exists:

```python
@pytest.mark.parametrize("status,is_revocation,invalidates", [
    ("active", False, False),
    ("rotated", True, False),
    ("retired", True, False),
    ("compromised", True, True),
])
def test_the_status_vocabulary_carries_its_rule_as_data(
        conn, status, is_revocation, invalidates):
    """The revocation rule lives in a TABLE an auditor can read, not in a Python
    if-statement -- the same shape as ci_axis.expands_descendants and
    class_expansion_policy. Asserted value by value: a seed that silently flipped
    `compromised` to non-invalidating would be the single most consequential wrong row
    in this schema, and no aggregate count would show it."""
    row = conn.execute(
        "SELECT is_revocation, invalidates_all_signatures "
        "FROM drugref.signing_key_status_kind WHERE status = %s", (status,)).fetchone()
    assert row == (is_revocation, invalidates)


def test_a_fifth_status_cannot_inherit_a_guess_about_either_boolean(conn):
    """NO DEFAULT on either column. class_expansion_policy's `allow` != absent and
    is_active_component's NULL != false are the same lesson; here the guess would decide
    whether a revocation destroys a curator's evidence."""
    with pytest.raises(psycopg.errors.NotNullViolation):
        conn.execute("INSERT INTO drugref.signing_key_status_kind (status, note) "
                     "VALUES ('suspended', 'x')")


def test_the_catalog_and_signing_py_agree_on_the_contexts(conn):
    """Two vocabularies that must not drift: a target kind whose context signing.py
    cannot encode is a row that makes `drugref sign` fail at the last moment, and a
    frozen field list no target kind names is dead code nothing exercises."""
    contexts = {row[0] for row in conn.execute(
        "SELECT payload_context FROM drugref.signature_target_kind").fetchall()}
    assert contexts == set(signing.FIELD_LISTS)


def test_signing_key_refuses_a_delete(conn):
    _key(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.signing_key WHERE key_fingerprint = %s", (FP,))


def test_signing_key_refuses_an_in_place_edit(conn):
    """Revocation is a CORRECTION -- insert the new status, point the old row at it --
    never an UPDATE. Editing in place would overwrite the history that makes 'was this
    key already revoked when that signature was made?' answerable at all."""
    _key(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.signing_key SET status = 'compromised' "
                     "WHERE key_fingerprint = %s", (FP,))


def test_signing_key_permits_only_superseded_by_to_change(conn):
    first = _key(conn)
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    second = _key(conn, status="compromised")
    conn.execute("UPDATE drugref.signing_key SET superseded_by = %s "
                 "WHERE signing_key_id = %s", (second, first))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT status FROM drugref.signing_key WHERE superseded_by IS NULL "
        "AND key_fingerprint = %s", (FP,)).fetchone()[0] == "compromised"


def test_two_live_rows_for_one_fingerprint_are_refused_at_commit(conn):
    """The single-live check is DEFERRED, so this fails at SET CONSTRAINTS IMMEDIATE
    rather than at the second INSERT. A test that never forces the check proves nothing
    -- Plan C's standing note -- which is why the immediate call is here."""
    _key(conn)
    conn.execute("SET CONSTRAINTS ALL DEFERRED")
    _key(conn, status="retired")
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_two_live_rows_for_DIFFERENT_fingerprints_coexist(conn):
    """The control for the test above: without it, a trigger that rejected EVERY second
    row would pass that one and forbid ever registering a second key."""
    _key(conn, FP)
    _key(conn, OTHER_FP)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert conn.execute(
        "SELECT count(*) FROM drugref.signing_key "
        "WHERE superseded_by IS NULL").fetchone()[0] == 2


@pytest.mark.parametrize("bad", ["ABC", "A" * 64, "a" * 63, "g" * 64, ""])
def test_a_malformed_fingerprint_is_refused(conn, bad):
    """The fingerprint is the identity a signature names, in a text column. A truncated
    or upper-case value is a row that silently matches no signature -- which looks
    exactly like a key nobody registered, and so reports UNKNOWN_KEY forever."""
    with pytest.raises(psycopg.errors.CheckViolation):
        _key(conn, fingerprint=bad)


def test_signing_key_is_discovered_as_an_eighth_single_live_table(conn):
    """DERIVED FROM THE CATALOG, not asserted as a literal eight. The gates round
    rebuilt the live-key coverage set from pg_trigger.tgargs precisely so a new table is
    guarded the day its migration lands with no list to edit. This asserts the
    derivation actually picked db/030's table up -- the property that matters."""
    from tests.test_live_key_index_guard import _single_live_tables
    tables = dict(_single_live_tables(conn))
    assert "signing_key" in tables
    assert tables["signing_key"] == "key_fingerprint"


def test_a_signature_cannot_be_deleted(conn):
    _signature(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.assertion_signature")


@pytest.mark.parametrize("column,value", [
    ("signed_at", NOW), ("key_fingerprint", OTHER_FP), ("target_id", 99),
    ("target_kind", "curated_condition"), ("payload_context", "curated_condition/v1"),
    ("payload_digest", b"\x09" * 32), ("signature", b"\x09" * 64),
    ("algorithm", "Ed25519"), ("recorded_at", NOW),
])
def test_no_column_of_a_signature_can_be_updated(conn, column, value):
    """STRICTER THAN forbid_overlay_rewrite, which exists to permit exactly one column
    to change. A signature has no superseded_by and needs none: a curator who mis-signed
    corrects the JUDGEMENT (a new curated row), and a key whose signatures must all be
    repudiated is handled at the KEY layer by `compromised`. A signature is a historical
    fact about a moment, not an assertion that can be revised.

    ONE TEST PER COLUMN rather than one for the table: a trigger comparing a SUBSET of
    columns is exactly the defect a single-column check would pass."""
    _signature(conn)
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(f"UPDATE drugref.assertion_signature SET {column} = %s", (value,))


def test_a_signature_of_the_wrong_length_is_refused(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO drugref.assertion_signature (target_kind, target_id, "
            "payload_context, payload_digest, key_fingerprint, algorithm, signature, "
            "signed_at) VALUES ('curated_interaction', 1, 'curated_interaction/v1', "
            "%s, %s, 'Ed25519', %s, %s)", (b"\x02" * 32, FP, b"\x03" * 10, NOW))


def test_an_unknown_target_kind_is_refused_by_the_foreign_key(conn):
    """An FK into signature_target_kind rather than a CHECK, for db/006's reason: the
    mapping from a kind to its table, key column and context has one home, so a fourth
    kind is one INSERT there rather than an edit in three places."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _signature(conn, kind="something_else")


def test_the_same_key_cannot_record_one_identical_attestation_twice(conn):
    _signature(conn)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _signature(conn)


def test_the_same_key_may_re_sign_the_same_target_at_a_later_moment(conn):
    """The control for the dedupe guard: a later signed_at yields a different payload
    and therefore a different digest, so a second row is legitimate and both are true.
    A uniqueness constraint on (kind, id, key) alone would forbid it."""
    _signature(conn, digest=b"\x02" * 32)
    _signature(conn, digest=b"\x05" * 32)
    assert conn.execute(
        "SELECT count(*) FROM drugref.assertion_signature").fetchone()[0] == 2


def test_a_manifest_is_insert_only(conn):
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.09', %s, 0, '[]'::jsonb, 'an operator', %s)",
        (b"\x04" * 32, NOW))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE drugref.release_manifest SET row_count = 1")


def test_a_manifest_cannot_be_deleted(conn):
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.10', %s, 0, '[]'::jsonb, 'an operator', %s)",
        (b"\x04" * 32, NOW))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM drugref.release_manifest")


def test_a_release_tag_cannot_be_reused(conn):
    conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.11', %s, 0, '[]'::jsonb, 'op', %s)", (b"\x04" * 32, NOW))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
            "row_count, upstream_releases, published_by, published_at) "
            "VALUES ('2026.08.11', %s, 0, '[]'::jsonb, 'op', %s)", (b"\x04" * 32, NOW))
```

- [ ] **Step 2: Run to verify failure**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_signing_schema.py -v`
Expected: every test FAILS with `UndefinedTable: relation "drugref.signing_key_status_kind" does not exist`.

- [ ] **Step 3: Write `db/030_signing.sql`, sections 1–3**

```sql
-- db/030_signing.sql -- slice 5c.4: signing the curated overlay.
--
-- TWO LAYERS OVER ONE MECHANISM. A per-row curator attestation and a per-release
-- institutional manifest are both `assertion_signature` rows over a canonical payload,
-- verified by one code path. That is the payoff of detaching the signature from the row
-- rather than adding a column to db/029 (spec 3): a column would have had to exist at
-- INSERT time, would have permitted exactly one signature per row, and would have done
-- nothing for the release layer.
--
-- WHAT SIGNING DOES NOT DO, stated here because the word invites over-reading: an
-- attacker with database write access can still INSERT unsigned curated rows (which
-- read `unsigned` -- the honest label), and a SUPERUSER can drop the triggers below
-- outright. That is issue 2's TRUNCATE + owner-role bypass and this file does not close
-- it. Signing converts "trust the database" into "trust the key holders", which is a
-- real reduction and not the same as making the database tamper-proof.

-- ============================================================================
-- 1. signing_key_status_kind -- the revocation rule, as DATA
-- ============================================================================
-- TWO BOOLEANS, AND THE SECOND IS NOT REDUNDANT. `invalidates_all_signatures` says
-- whether a revocation destroys evidence retrospectively; `is_revocation` says whether
-- `status_from` is an END boundary at all. Without the second, an active key's
-- status_from -- its registration time -- would expire every signature it ever made,
-- because every signature is necessarily later than the registration.
--
-- HELD AS DATA rather than as a CHECK plus a Python if-statement, for db/006's reason,
-- now applied a fifth time: the rule a verifier branches on is exactly the thing that
-- drifts when it is written down twice. ci_axis.expands_descendants and
-- class_expansion_policy are the precedents -- a rule a pharmacist can read.
CREATE TABLE IF NOT EXISTS drugref.signing_key_status_kind (
    status                     text    PRIMARY KEY,
    is_revocation              boolean NOT NULL,
    invalidates_all_signatures boolean NOT NULL,
    note                       text    NOT NULL
);

INSERT INTO drugref.signing_key_status_kind
    (status, is_revocation, invalidates_all_signatures, note)
VALUES
    ('active', false, false,
     'In use. status_from is the registration time, not an expiry.'),
    ('rotated', true, false,
     'Replaced by a new key -- a new laptop, a scheduled rotation. TIME-SCOPED: '
     'signatures made before status_from still verify, because the holder''s prior '
     'work is unaffected by their changing keys.'),
    ('retired', true, false,
     'The holder is no longer curating. Time-scoped for the same reason as rotated: '
     'a curator leaving does not make their past clinical judgements unsound.'),
    ('compromised', true, true,
     'The private key may be in other hands. BLANKET: every signature this key ever '
     'made is suspect regardless of signed_at, because after a compromise there is no '
     'way to tell the holder''s signatures from the attacker''s. The consequence is a '
     're-review queue, not a silent mass invalidation -- the read views keep serving '
     'these rows, labelled.')
ON CONFLICT (status) DO NOTHING;

COMMENT ON TABLE drugref.signing_key_status_kind IS
    'The revocation rule as data. is_revocation says whether status_from is an END '
    'boundary (an active key''s is its registration time, so without this every '
    'signature ever made would expire); invalidates_all_signatures says whether the '
    'revocation destroys evidence retrospectively. NEITHER HAS A DEFAULT -- a fifth '
    'status must not inherit a guess about whether it invalidates a curator''s work.';

-- ============================================================================
-- 2. signature_target_kind -- what a signature may point at
-- ============================================================================
-- ONE HOME for the mapping from a target kind to its table, key column and canonical
-- context, so a fourth kind is one INSERT here rather than an edit in Python, in SQL
-- and in a CHECK.
CREATE TABLE IF NOT EXISTS drugref.signature_target_kind (
    target_kind     text PRIMARY KEY,
    target_table    text NOT NULL,
    pk_column       text NOT NULL,
    payload_context text NOT NULL
);

INSERT INTO drugref.signature_target_kind
    (target_kind, target_table, pk_column, payload_context)
VALUES
    ('curated_interaction', 'curated_interaction', 'curated_interaction_id',
     'curated_interaction/v1'),
    ('curated_condition', 'curated_condition', 'curated_condition_id',
     'curated_condition/v1'),
    ('release_manifest', 'release_manifest', 'manifest_id', 'release_manifest/v1')
ON CONFLICT (target_kind) DO NOTHING;

-- ============================================================================
-- 3. signing_key -- the registry, on db/020's overlay floor (its EIGHTH table)
-- ============================================================================
-- REVOCATION IS A CORRECTION, not a column edit: INSERT the new status, then point the
-- live row at it via overlay.supersede. The full status history of a key is therefore
-- readable, which is the only thing that makes "was this key already revoked when that
-- signature was made?" answerable.
--
-- The natural key is `key_fingerprint` and it is deliberately NOT UNIQUE -- a correction
-- keeps the same key by definition, so both rows are briefly live. The partial index
-- plus the deferred trigger enforce single-live; adding UNIQUE (key_fingerprint) "for
-- safety" would forbid every revocation. db/027's note, one table on.
CREATE TABLE IF NOT EXISTS drugref.signing_key (
    signing_key_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key_fingerprint text        NOT NULL,
    public_key      bytea       NOT NULL,
    algorithm       text        NOT NULL,
    -- FREE TEXT, and deliberately not constrained to match curated_*.reviewed_by.
    -- Enforcing the match would put one string in two places under a constraint a
    -- legitimate name change breaks; a verifier reports both, and a mismatch is a fact
    -- a consumer can act on rather than an error the schema should refuse.
    holder          text        NOT NULL,
    status          text        NOT NULL
                                REFERENCES drugref.signing_key_status_kind(status),
    status_from     timestamptz NOT NULL,
    registered_by   text        NOT NULL,
    registered_at   timestamptz NOT NULL DEFAULT now(),
    superseded_by   bigint      REFERENCES drugref.signing_key(signing_key_id),
    CONSTRAINT signing_key_algorithm CHECK (algorithm IN ('Ed25519')),
    -- THE FINGERPRINT IS THE IDENTITY A SIGNATURE NAMES, in a text column. A truncated
    -- or upper-case value is a row that silently matches no signature -- which is
    -- indistinguishable from a key nobody registered, so it reports UNKNOWN_KEY forever
    -- rather than failing loudly.
    CONSTRAINT signing_key_fingerprint_shape
        CHECK (key_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT signing_key_public_key_length CHECK (octet_length(public_key) = 32)
);

DROP TRIGGER IF EXISTS signing_key_append_only ON drugref.signing_key;
CREATE TRIGGER signing_key_append_only
    BEFORE UPDATE OR DELETE ON drugref.signing_key
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_overlay_rewrite(
        'signing_key_id', 'key_fingerprint');

-- DEFERRED, because a correction is momentarily TWO live rows -- between the INSERT and
-- the UPDATE that supersedes -- and an immediate check would reject the only sequence
-- that can express one.
DROP TRIGGER IF EXISTS signing_key_single_live ON drugref.signing_key;
CREATE CONSTRAINT TRIGGER signing_key_single_live
    AFTER INSERT OR UPDATE ON drugref.signing_key
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_multiple_live_assertions(
        'key_fingerprint');

-- PARTIAL and NOT UNIQUE, matching the trigger's predicate exactly. Nothing but the
-- trigger reads it, so a test asserts it by name -- and since the gates round the
-- covered set is DERIVED from pg_trigger.tgargs, so this table is picked up
-- automatically rather than by editing three literal lists.
CREATE INDEX IF NOT EXISTS signing_key_live_key
    ON drugref.signing_key (key_fingerprint)
    WHERE superseded_by IS NULL;

COMMENT ON TABLE drugref.signing_key IS
    'CURATED, APPEND-ONLY: the public keys drugref trusts, and their status history. '
    'The private half NEVER enters this database or any drugref infrastructure -- that '
    'is the whole point of the row layer, and it is what an insider with full write '
    'access cannot forge. Revocation is a correction (insert, then supersede), never an '
    'UPDATE, so the history that DATES a revocation survives. THE TRUST ROOT IS AN '
    'OPERATOR: a key is trusted because someone with database access registered it. '
    'There is no enrolment protocol and no certificate chain.';
```

- [ ] **Step 4: Write `db/030_signing.sql`, sections 4–6**

```sql
-- ============================================================================
-- 4. forbid_any_rewrite -- strictly insert-only
-- ============================================================================
-- STRICTER THAN forbid_overlay_rewrite, which exists to permit exactly one column
-- (superseded_by) to change. The three tables below have no superseded_by and need
-- none -- and the question was ASKED rather than assumed, because this project's
-- standing finding is that supersession alone withdraws nothing, and four tables have
-- needed a ruling column for it (additive_effect.accumulates,
-- interaction_group_member.satisfies_role, interaction_group_assertion.applies,
-- class_expansion_policy.decision = 'withdrawn').
--
-- THE ANSWER FOR A SIGNATURE IS THAT RETRACTION HAPPENS IN THE LAYERS EITHER SIDE OF
-- IT, never here. A curator who signed a judgement they now disagree with corrects the
-- JUDGEMENT -- a new curated row, the predecessor superseded and out of the read path
-- -- and the old signature remains a true statement about what they attested on that
-- date, which is exactly what a row that fired alerts for six months needs. A key whose
-- signatures must all be repudiated is handled at the key layer by `compromised`. A
-- signature is a historical fact about a moment, not an assertion that can be revised.
CREATE OR REPLACE FUNCTION drugref.forbid_any_rewrite() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'drugref.% is insert-only: % forbidden', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION drugref.forbid_any_rewrite() IS
    'The insert-only floor: no UPDATE of any column, no DELETE, ever. Distinct from '
    'forbid_overlay_rewrite, which permits superseded_by to change. For tables whose '
    'rows are historical FACTS rather than revisable assertions.';

-- ============================================================================
-- 5. assertion_signature
-- ============================================================================
-- target_id IS A POINTER, NOT CONTENT, and is deliberately absent from the signed
-- payload: GENERATED ALWAYS AS IDENTITY values are local to one database, so signing
-- one would break a signature carried into another. Verification re-derives the payload
-- from the row's CONTENT and checks the signature over that.
--
-- signed_at IS INSIDE THE SIGNED PAYLOAD, so it cannot be edited to walk a signature
-- across a revocation boundary. recorded_at is this database's own clock and is NOT
-- signed -- the gap between the two is a backdating signal, reported by
-- signature_backdated.
CREATE TABLE IF NOT EXISTS drugref.assertion_signature (
    signature_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_kind     text        NOT NULL
                                REFERENCES drugref.signature_target_kind(target_kind),
    target_id       bigint      NOT NULL,
    payload_context text        NOT NULL,
    payload_digest  bytea       NOT NULL,
    -- NO FOREIGN KEY into signing_key, and that is deliberate. A signature naming a key
    -- nobody has registered is an ORDINARY finding -- it is the UNKNOWN_KEY verdict --
    -- and an FK would make recording it impossible, which would mean a node could not
    -- even store the evidence that an unknown key signed something.
    key_fingerprint text        NOT NULL,
    algorithm       text        NOT NULL,
    signature       bytea       NOT NULL,
    signed_at       timestamptz NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT assertion_signature_algorithm CHECK (algorithm IN ('Ed25519')),
    CONSTRAINT assertion_signature_digest_length
        CHECK (octet_length(payload_digest) = 32),
    CONSTRAINT assertion_signature_length CHECK (octet_length(signature) = 64),
    CONSTRAINT assertion_signature_fingerprint_shape
        CHECK (key_fingerprint ~ '^[0-9a-f]{64}$'),
    -- A DEDUPE GUARD, not an identity: re-signing with a later signed_at yields a
    -- different payload and therefore a different digest, so a second row is legitimate
    -- and both are true. This refuses only recording the SAME attestation twice.
    CONSTRAINT assertion_signature_unique
        UNIQUE (target_kind, target_id, key_fingerprint, payload_digest)
);

DROP TRIGGER IF EXISTS assertion_signature_insert_only ON drugref.assertion_signature;
CREATE TRIGGER assertion_signature_insert_only
    BEFORE UPDATE OR DELETE ON drugref.assertion_signature
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

-- The lookup every verification does, and the one the read views join on. Read only by
-- the planner, so a test asserts it by name -- as with the live-key indexes.
CREATE INDEX IF NOT EXISTS assertion_signature_by_target
    ON drugref.assertion_signature (target_kind, target_id);
-- The lookup a key revocation does: "what did this key sign?" is the re-review queue a
-- compromise produces, and without this it is a sequential scan.
CREATE INDEX IF NOT EXISTS assertion_signature_by_key
    ON drugref.assertion_signature (key_fingerprint);

COMMENT ON TABLE drugref.assertion_signature IS
    'INSERT-ONLY: no UPDATE of any column, no DELETE. A signature is a historical fact '
    'about a moment, not a revisable assertion -- a mis-signed judgement is corrected '
    'at the curated row, and a compromised key is repudiated at signing_key. target_id '
    'is a POINTER and is NOT in the signed payload: identity values are local to a '
    'database and the signature must survive being carried into another. signed_at IS '
    'signed; recorded_at is not, and the gap between them is what signature_backdated '
    'reports. NO FK to signing_key: a signature by an unregistered key is the '
    'UNKNOWN_KEY verdict, and must be storable.';

-- ============================================================================
-- 6. release_manifest + release_manifest_entry
-- ============================================================================
-- A CONTENT MANIFEST, not a signature over shipped bytes. A transport signature dies at
-- load time -- once the data is in a database it can never be re-checked against those
-- bytes -- and "is this table still what drugref published?" is the question that
-- matters for the following several years. Because the manifest ENUMERATES,
-- verification is bidirectional and catches OMISSION as well as alteration.
CREATE TABLE IF NOT EXISTS drugref.release_manifest (
    manifest_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- drugref's OWN version string, STATED by the operator and never derived, exactly
    -- as ingest_run's release tags are (PROJECT-NOTES: "stated, never parsed from a
    -- filename"). UNIQUE, so one tag cannot name two manifests.
    release_tag       text        NOT NULL UNIQUE,
    manifest_digest   bytea       NOT NULL,
    -- REDUNDANT WITH THE ENTRIES ON PURPOSE: a group truncated at its END is otherwise
    -- detectable only by recomputing the whole digest, and a scalar count makes that
    -- specific failure nameable.
    row_count         integer     NOT NULL,
    upstream_releases jsonb       NOT NULL,
    published_by      text        NOT NULL,
    published_at      timestamptz NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT release_manifest_row_count CHECK (row_count >= 0),
    CONSTRAINT release_manifest_digest_length
        CHECK (octet_length(manifest_digest) = 32)
);

-- KEYED ON THE NATURAL KEY, NEVER ON target_id. target_id is a database-local
-- GENERATED ALWAYS AS IDENTITY value (section 5 says why it stays out of a signed
-- payload), so keying a manifest on it would break the release layer in exactly the
-- situation it exists for: a node that REBUILT rather than restored assigns different
-- identity values, and every entry would fail to match. natural_key is stable across
-- databases because moiety_uuid is immortal and class_uuid/condition_uuid are
-- deterministic UUIDv5 mints -- and it is what makes `altered` nameable at all, since
-- pairing on the digest alone could only ever report one drop plus one addition and
-- leave a consumer to guess whether they were the same row.
--
-- target_id survives as an UNSIGNED convenience column so an operator can join an entry
-- back to the local row it describes. Nothing verifies against it.
CREATE TABLE IF NOT EXISTS drugref.release_manifest_entry (
    manifest_id     bigint NOT NULL REFERENCES drugref.release_manifest(manifest_id),
    target_kind     text   NOT NULL
                           REFERENCES drugref.signature_target_kind(target_kind),
    natural_key     text   NOT NULL,
    target_id       bigint NOT NULL,
    payload_context text   NOT NULL,
    payload_digest  bytea  NOT NULL,
    PRIMARY KEY (manifest_id, target_kind, natural_key),
    CONSTRAINT release_manifest_entry_digest_length
        CHECK (octet_length(payload_digest) = 32)
);

DROP TRIGGER IF EXISTS release_manifest_insert_only ON drugref.release_manifest;
CREATE TRIGGER release_manifest_insert_only
    BEFORE UPDATE OR DELETE ON drugref.release_manifest
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

DROP TRIGGER IF EXISTS release_manifest_entry_insert_only
    ON drugref.release_manifest_entry;
CREATE TRIGGER release_manifest_entry_insert_only
    BEFORE UPDATE OR DELETE ON drugref.release_manifest_entry
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_any_rewrite();

COMMENT ON TABLE drugref.release_manifest IS
    'INSERT-ONLY. One published drugref release: an enumeration of every live curated '
    'assertion at publication with its content digest, plus a snapshot of which '
    'upstream releases were loaded. Signed by the institutional key as an '
    'assertion_signature row with target_kind = ''release_manifest'' -- ONE mechanism '
    'carries both the row layer and this one. Verification is BIDIRECTIONAL: a row the '
    'manifest lists and the database lacks is a DROP, a live row the manifest omits is '
    'an ADDITION, and a digest mismatch is an ALTERATION.';
```

- [ ] **Step 5: Run the schema tests**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_signing_schema.py -v`
Expected: all pass. The `_migrated` fixture drops the schema and re-applies every migration each session, so
`db/030` is picked up with no manual step.

- [ ] **Step 6: Confirm the live-key derivation picked up the eighth table**

Run: `DRUGREF_TEST_DSN='...' uv run pytest tests/test_live_key_index_guard.py -v`
Expected: `test_every_single_live_trigger_has_a_matching_index` still passes, **now covering eight tables**. If it
fails, `signing_key_live_key`'s columns do not match the trigger's `tgargs` — **fix the index, not the test.**

- [ ] **Step 7: Full suite, lint, commit**

```bash
DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest -q
uv run ruff check .
git add db/030_signing.sql tests/test_signing_schema.py
git commit -m "feat: db/030 -- the signing tables, on the existing floors

signing_key takes db/020's overlay floor as its EIGHTH table, with NO new
PL/pgSQL: revocation is a correction (insert the new status, supersede the
old), so a key's status history survives and 'was this key already revoked
when that signature was made?' stays answerable. Because the gates round
derives the live-key covered set from pg_trigger.tgargs, the new table is
guarded the day this migration lands with no list edited.

assertion_signature needs a STRICTER floor than forbid_overlay_rewrite,
which exists to let superseded_by change. The 'what does withdrawing one of
these look like?' question was asked rather than skipped -- four tables have
needed a ruling column for it -- and the answer is that retraction happens
one layer either side: a mis-signed judgement is corrected at the curated
row, a compromised key is repudiated at signing_key.

No FK from assertion_signature to signing_key: a signature by a key nobody
registered is the UNKNOWN_KEY verdict, and a node must be able to STORE the
evidence that one exists.

The revocation rule is DATA, two booleans, neither with a DEFAULT.
is_revocation is not redundant: an active key's status_from is its
registration time, so without it every signature ever made would expire.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `keys.py` — the registry writer

**Files:**
- Create: `src/drugref/keys.py`
- Test: `tests/test_keys_writer.py`

**Interfaces:**
- Consumes: `signing.fingerprint`, `signing.ED25519`, `signing.KeyStatus` (Tasks 2, 4); `overlay.supersede`.
- Produces: `keys.KeyRecord` (frozen dataclass, fields in `_COLUMNS` order: `signing_key_id: int`,
  `key_fingerprint: str`, `public_key: bytes`, `algorithm: str`, `holder: str`, `status: str`,
  `status_from: datetime`, `registered_by: str`, `registered_at: datetime`, `superseded_by: int | None`) ·
  `keys.register(conn, *, public_key: bytes, holder: str, registered_by: str, algorithm: str = signing.ED25519,
  status: str = "active", status_from: datetime | None = None) -> int` ·
  `keys.revoke(conn, *, key_fingerprint: str, status: str, revoked_by: str, status_from: datetime | None = None)
  -> int` · `keys.live(conn, key_fingerprint) -> KeyRecord | None` ·
  `keys.key_status(conn, key_fingerprint) -> signing.KeyStatus | None` · `keys.all_live(conn) -> list[KeyRecord]` ·
  `keys.history(conn, key_fingerprint) -> list[KeyRecord]` · `keys.NoLiveKeyError(RuntimeError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_keys_writer.py`:

```python
# tests/test_keys_writer.py
"""The signing_key registry writer (spec 5.1, 6). DB-gated."""
import datetime as dt
import inspect

import pytest

from drugref import keys, signing

NOW = dt.datetime(2026, 8, 9, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def a_key(conn):
    _, public = signing.generate_keypair()
    keys.register(conn, public_key=public, holder="a curator",
                  registered_by="an operator", status_from=NOW)
    return public


def test_a_registered_key_is_live_and_findable_by_fingerprint(conn, a_key):
    record = keys.live(conn, signing.fingerprint(a_key))
    assert record is not None
    assert record.holder == "a curator"
    assert record.status == "active"
    assert record.public_key == a_key
    assert record.status_from == NOW


def test_an_unregistered_fingerprint_reads_as_None_not_an_error(conn):
    """A signature naming a key nobody registered is an ORDINARY finding -- it is the
    UNKNOWN_KEY verdict -- so the read returns None and the verdict rule decides. An
    exception here would force every verification path to wrap it, and a caller would
    eventually wrap too widely."""
    assert keys.live(conn, "f" * 64) is None
    assert keys.key_status(conn, "f" * 64) is None


def test_register_derives_the_fingerprint_rather_than_accepting_one():
    """register() takes the PUBLIC KEY. Accepting a fingerprint as well would let a
    caller store one that does not match its key -- a row that verifies nothing, reports
    UNKNOWN_KEY forever, and looks entirely healthy in every listing."""
    assert "key_fingerprint" not in inspect.signature(keys.register).parameters


def test_revoking_supersedes_rather_than_editing(conn, a_key):
    fp = signing.fingerprint(a_key)
    first = keys.live(conn, fp).signing_key_id
    keys.revoke(conn, key_fingerprint=fp, status="compromised",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert keys.live(conn, fp).status == "compromised"
    history = keys.history(conn, fp)
    assert [r.status for r in history] == ["active", "compromised"]
    assert history[0].signing_key_id == first
    assert history[0].superseded_by == history[1].signing_key_id


def test_revocation_carries_the_key_material_and_holder_forward(conn, a_key):
    """The new row is the SAME key with a new status, so it must carry public_key,
    algorithm and holder forward -- exactly as withdraw_expansion_decision carries
    class_name forward. Taking them from the caller instead would let a revocation
    quietly re-attribute a key to somebody else."""
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="retired",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    record = keys.live(conn, fp)
    assert record.public_key == a_key
    assert record.holder == "a curator"
    assert record.algorithm == signing.ED25519
    assert record.registered_by == "an operator"


def test_revoking_a_key_nobody_registered_raises(conn):
    """NoLiveKeyError rather than a silent no-op, on withdraw_expansion_decision's
    precedent: an operator revoking a typo'd fingerprint has been told nothing and would
    reasonably believe the key is now revoked. That is the worst possible outcome of a
    revocation command."""
    with pytest.raises(keys.NoLiveKeyError):
        keys.revoke(conn, key_fingerprint="c" * 64, status="compromised",
                    revoked_by="an operator")


def test_key_status_assembles_the_rule_from_the_vocabulary_table(conn, a_key):
    """The two booleans come from signing_key_status_kind, never from a Python mapping
    -- that is the whole reason db/030 holds them as data."""
    fp = signing.fingerprint(a_key)
    assert keys.key_status(conn, fp) == signing.KeyStatus(
        "active", is_revocation=False, invalidates_all_signatures=False,
        status_from=NOW)
    keys.revoke(conn, key_fingerprint=fp, status="compromised",
                revoked_by="op", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    revoked = keys.key_status(conn, fp)
    assert revoked.invalidates_all_signatures is True
    assert revoked.is_revocation is True
    assert revoked.status_from == LATER


def test_nothing_here_commits(conn, a_key):
    """The caller owns the transaction, as everywhere in these modules. Proved by
    rolling back and finding nothing rather than by reading the source."""
    conn.rollback()
    assert keys.live(conn, signing.fingerprint(a_key)) is None


def test_two_keys_for_one_holder_coexist(conn, a_key):
    """A rotation registers a NEW key beside the old one. `holder` is not a natural key,
    and two live keys for one person is an ordinary state during a rotation."""
    _, second = signing.generate_keypair()
    keys.register(conn, public_key=second, holder="a curator",
                  registered_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert len(keys.all_live(conn)) == 2


def test_history_is_oldest_first_and_totally_ordered(conn, a_key):
    """Matching interactions.decision_history. Totally ordered on the surrogate key
    rather than on status_from, which an operator may supply out of order."""
    fp = signing.fingerprint(a_key)
    keys.revoke(conn, key_fingerprint=fp, status="rotated", revoked_by="op",
                status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    ids = [r.signing_key_id for r in keys.history(conn, fp)]
    assert ids == sorted(ids)
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: No module named 'drugref.keys'`.

- [ ] **Step 3: Write `src/drugref/keys.py`**

```python
# src/drugref/keys.py
"""The signing-key registry: who drugref trusts to sign a curated judgement (db/030).

THE PRIVATE HALF NEVER ENTERS THIS DATABASE, or any drugref infrastructure. That is the
entire value of the row layer: an insider with total write access can still type any
name into curated_*.reviewed_by, but cannot produce a signature over the row. Store a
private key here and the layer proves exactly what the unauthenticated text column
already claimed.

THE TRUST ROOT IS AN OPERATOR. A public key is trusted because somebody with database
access ran `drugref keys register`. There is no enrolment protocol, no web of trust and
no certificate chain -- a real limitation, recorded in the spec rather than papered over.

REVOCATION IS A CORRECTION, not an edit: INSERT the new status, then point the live row
at it through overlay.supersede -- the same sequence every curated table uses, and the
reason a key's status history survives at all. Without that history, "was this key
already revoked when that signature was made?" has no answer, and the whole time-scoped
half of the revocation model collapses.

NOTHING HERE COMMITS. The caller owns the transaction, as everywhere in these modules,
and the single-live check is DEFERRED -- so registering a second live row for one
fingerprint surfaces at the caller's COMMIT, not here.
"""
import datetime as dt
import uuid  # noqa: F401  -- kept out; see note below if unused
from dataclasses import dataclass

import psycopg

from drugref import overlay, signing


class NoLiveKeyError(RuntimeError):
    """Revoking a key with no live row. Raised rather than no-op'ing.

    interactions.NoLiveDecisionError's precedent, and the argument is the same one
    turned up a notch: an operator who mistypes a fingerprint and is told nothing walks
    away believing a compromised key has been revoked. Silence is the worst answer a
    revocation command can give.
    """


# THE ONE COLUMN LIST, generating the SELECT and binding the record BY KEYWORD --
# curation._UNRESOLVED_COLUMNS' shape and its reason. Positionally, `key_fingerprint`,
# `algorithm`, `holder`, `status` and `registered_by` are all text and the two
# timestamps are interchangeable, so a transposition builds a WELL-TYPED WRONG record
# that no annotation and no arity check can see. Binding by name removes the failure
# mode instead of testing for it; strict=True catches a column gained or lost.
_COLUMNS = ("signing_key_id", "key_fingerprint", "public_key", "algorithm", "holder",
            "status", "status_from", "registered_by", "registered_at", "superseded_by")


@dataclass(frozen=True)
class KeyRecord:
    """One row of signing_key -- a key's state at one point in its history."""
    signing_key_id: int
    key_fingerprint: str
    public_key: bytes
    algorithm: str
    holder: str
    status: str
    status_from: dt.datetime
    registered_by: str
    registered_at: dt.datetime
    superseded_by: int | None


def _record(row) -> KeyRecord:
    values = dict(zip(_COLUMNS, row, strict=True))
    # psycopg returns bytea as `memoryview`; the caller compares it against the bytes it
    # generated, and memoryview(b"a") != b"a" is False but `== ` is True only after a
    # cast. Normalise once, here, rather than at every call site.
    values["public_key"] = bytes(values["public_key"])
    return KeyRecord(**values)


_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM drugref.signing_key "


def register(conn: psycopg.Connection, *, public_key: bytes, holder: str,
             registered_by: str, algorithm: str = signing.ED25519,
             status: str = "active",
             status_from: dt.datetime | None = None) -> int:
    """Register a public key. Returns the new signing_key_id.

    TAKES THE KEY, DERIVES THE FINGERPRINT. Accepting both would let a caller store a
    fingerprint that does not match its key -- a row that matches no signature, reports
    UNKNOWN_KEY forever, and looks perfectly healthy in `drugref keys list`.

    `status_from` defaults to the database's `now()` rather than to a Python clock, so a
    registration cannot be dated by a machine whose time is wrong relative to the server
    that stamps `registered_at` beside it. It is settable so a test can pin an instant.
    """
    return conn.execute(
        "INSERT INTO drugref.signing_key (key_fingerprint, public_key, algorithm, "
        "holder, status, status_from, registered_by) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s) "
        "RETURNING signing_key_id",
        (signing.fingerprint(public_key), public_key, algorithm, holder, status,
         status_from, registered_by)).fetchone()[0]


def revoke(conn: psycopg.Connection, *, key_fingerprint: str, status: str,
           revoked_by: str, status_from: dt.datetime | None = None) -> int:
    """Change a key's status by CORRECTION. Returns the new signing_key_id.

    Raises NoLiveKeyError if nothing is live for that fingerprint.

    THE KEY MATERIAL AND HOLDER ARE CARRIED FORWARD from the live row, never re-supplied
    by the caller -- withdraw_expansion_decision carries class_name forward for exactly
    this reason. Taking them as arguments would let a revocation quietly re-attribute a
    key to a different holder, under the one command an operator runs when they are
    already alarmed.

    `revoked_by` lands in `registered_by`: the column records WHO PUT THIS ROW HERE, and
    for a revocation that is the revoker. A separate `revoked_by` column would be a
    second name for one fact, NULL on every other row.
    """
    current = live(conn, key_fingerprint)
    if current is None:
        raise NoLiveKeyError(
            f"no live signing key for fingerprint {key_fingerprint}. Nothing was "
            "changed. Check `drugref keys list` -- a mistyped fingerprint here would "
            "otherwise leave you believing a key had been revoked.")
    new_id = conn.execute(
        "INSERT INTO drugref.signing_key (key_fingerprint, public_key, algorithm, "
        "holder, status, status_from, registered_by) "
        "VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s) "
        "RETURNING signing_key_id",
        (current.key_fingerprint, current.public_key, current.algorithm,
         current.holder, status, status_from, revoked_by)).fetchone()[0]
    overlay.supersede(conn, "signing_key", "signing_key_id", new_id,
                      ("key_fingerprint",), (key_fingerprint,))
    return new_id


def live(conn: psycopg.Connection, key_fingerprint: str) -> KeyRecord | None:
    """The key's current row, or None if no key with that fingerprint is registered.

    NONE IS NOT AN ERROR: a signature naming an unregistered key is the UNKNOWN_KEY
    verdict, which is an ordinary thing for a verifier to report.
    """
    row = conn.execute(
        _SELECT + "WHERE key_fingerprint = %s AND superseded_by IS NULL",
        (key_fingerprint,)).fetchone()
    return _record(row) if row else None


def key_status(conn: psycopg.Connection,
               key_fingerprint: str) -> signing.KeyStatus | None:
    """What the verdict rule needs to know about a key. None if unregistered.

    THE TWO BOOLEANS COME FROM signing_key_status_kind, never from a Python mapping over
    `status`. That table is where the revocation rule lives (db/030 section 1), and a
    second copy here is the defect db/006 named and four rounds have paid for.
    """
    row = conn.execute(
        "SELECT k.status, t.is_revocation, t.invalidates_all_signatures, k.status_from "
        "FROM drugref.signing_key k "
        "JOIN drugref.signing_key_status_kind t ON t.status = k.status "
        "WHERE k.key_fingerprint = %s AND k.superseded_by IS NULL",
        (key_fingerprint,)).fetchone()
    if row is None:
        return None
    status, is_revocation, invalidates, status_from = row
    return signing.KeyStatus(status, is_revocation=is_revocation,
                             invalidates_all_signatures=invalidates,
                             status_from=status_from)


def all_live(conn: psycopg.Connection) -> list[KeyRecord]:
    """Every currently-registered key, ordered by holder then fingerprint.

    TOTALLY ORDERED, on curation.unresolved_targets' precedent: several keys for one
    holder is the ordinary state during a rotation, and those tie on `holder` alone --
    which would leave `drugref keys list` printing a different order run to run and any
    multi-row test flaking.
    """
    return [_record(row) for row in conn.execute(
        _SELECT + "WHERE superseded_by IS NULL ORDER BY holder, key_fingerprint"
        ).fetchall()]


def history(conn: psycopg.Connection, key_fingerprint: str) -> list[KeyRecord]:
    """One key's whole status history, OLDEST FIRST.

    Ordered on the surrogate key rather than on `status_from`, which an operator may
    supply out of order (dating a revocation to when the laptop was actually stolen is
    the realistic case). The surrogate key is the order the rows were WRITTEN, which is
    the one the supersession chain follows.
    """
    return [_record(row) for row in conn.execute(
        _SELECT + "WHERE key_fingerprint = %s ORDER BY signing_key_id",
        (key_fingerprint,)).fetchall()]
```

**Remove the `import uuid` line** — it is in the sketch above only to mark that no UUID is used here; ruff's `F401`
will fail on it, which is the intended reminder.

- [ ] **Step 4: Run the tests, full suite, lint**

Run: `DRUGREF_TEST_DSN='host=localhost port=5532 dbname=drugref_test user=postgres' uv run pytest tests/test_keys_writer.py -v` → 10 passed.
Then the full suite and `uv run ruff check .`.

- [ ] **Step 5: Commit**

```bash
git add src/drugref/keys.py tests/test_keys_writer.py
git commit -m "feat: the signing key registry writer

register() takes the PUBLIC KEY and derives the fingerprint rather than
accepting both: a caller-supplied fingerprint can disagree with its key,
producing a row that matches no signature, reports UNKNOWN_KEY forever and
looks entirely healthy in every listing.

revoke() carries public_key, algorithm and holder forward from the live row
-- withdraw_expansion_decision's precedent -- because re-supplying them
would let a revocation quietly re-attribute a key, under the one command an
operator runs when they are already alarmed. It raises NoLiveKeyError rather
than no-op'ing: silence is the worst answer a revocation can give.

key_status() reads both booleans from signing_key_status_kind. A Python
mapping over `status` would be the second-vocabulary defect db/006 named.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `signatures.py` — build the payload, record a signature, verify one target

**Files:**
- Create: `src/drugref/signatures.py`
- Test: `tests/test_signatures_writer.py`

**Interfaces:**
- Consumes: `signing.canonical_payload`, `render`, `FIELD_LISTS`, `ATTESTATION_FIELDS`, `digest`, `verify`,
  `verdict`, `ED25519`, the verdict constants; `keys.key_status`, `keys.live`.
- Produces: `signatures.payload_for(conn, target_kind: str, target_id: int, *, key_fingerprint: str, signed_at:
  datetime) -> tuple[str, bytes]` returning `(payload_context, payload_bytes)` ·
  `signatures.record(conn, *, target_kind, target_id, payload_context, payload: bytes, key_fingerprint,
  signature: bytes, signed_at, algorithm=signing.ED25519) -> int` ·
  `signatures.SignatureVerdict` (frozen dataclass: `signature_id: int`, `key_fingerprint: str`,
  `holder: str | None`, `signed_at: datetime`, `verdict: str`) ·
  `signatures.verify_target(conn, target_kind, target_id) -> list[SignatureVerdict]` ·
  `signatures.UnknownTargetError(RuntimeError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_signatures_writer.py`. Shared fixture first — it signs one real curated interaction:

```python
# tests/test_signatures_writer.py
"""Building, recording and verifying a per-row signature (spec 4.4, 7.1). DB-gated."""
import datetime as dt

import pytest

from drugref import curation, keys, signatures, signing

SIGNED_AT = dt.datetime(2026, 8, 9, 4, 33, 52, 8117, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc)


@pytest.fixture
def signed_rule(conn, a_graded_rule):
    """One curated interaction judgement, signed by one registered curator key.

    Builds on conftest's `a_graded_rule`, which sets up the CI_MoA rule and its
    membership but deliberately does not grade it -- the grading is this fixture's job.
    """
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a curator",
                  registered_by="an operator")
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", mechanism="additive effect", management="monitor",
        evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", target_id,
        key_fingerprint=fingerprint, signed_at=SIGNED_AT)
    signature = signing.sign(private, payload)
    signatures.record(conn, target_kind="curated_interaction", target_id=target_id,
                      payload_context=context, payload=payload,
                      key_fingerprint=fingerprint, signature=signature,
                      signed_at=SIGNED_AT)
    return {"target_id": target_id, "private": private, "public": public,
            "fingerprint": fingerprint, "payload": payload, "signature": signature,
            **a_graded_rule}
```

Then the behaviour tests:

```python
def test_a_recorded_signature_verifies(conn, signed_rule):
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID]
    assert verdicts[0].holder == "a curator"


def test_an_unsigned_target_reports_no_signature(conn, a_graded_rule):
    """UNSIGNED IS AN ORDINARY STATE, not an error: signing is optional per row and the
    overlay ships empty. A verifier that raised here would make the normal case fail."""
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="minor", evidence_grade="theoretical", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    assert signatures.verify_target(conn, "curated_interaction", target_id) == []


def test_the_payload_context_comes_from_the_catalog(conn, signed_rule):
    """payload_for reads target_table, pk_column and payload_context from
    signature_target_kind -- never from a hardcoded mapping in Python. A fourth target
    kind must be one INSERT there, not an edit here and there and in a CHECK."""
    context, _ = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=signed_rule["fingerprint"], signed_at=SIGNED_AT)
    assert context == "curated_interaction/v1"


def test_a_missing_target_row_raises_rather_than_signing_nothing(conn):
    """Signing a row that does not exist would produce a payload of NULLs -- a valid
    signature over nothing, indistinguishable from a real one at a glance."""
    with pytest.raises(signatures.UnknownTargetError):
        signatures.payload_for(conn, "curated_interaction", 999_999,
                               key_fingerprint="a" * 64, signed_at=SIGNED_AT)


def test_the_signature_survives_the_row_being_superseded(conn, signed_rule):
    """A correction inserts a NEW row and points this one at it. The old signature is
    still a true statement about what the curator attested on that date -- and for a row
    that fired alerts for six months, that is the record that matters most."""
    curation.record_interaction_judgement(
        conn, signed_rule["subject"], signed_rule["class"], "CI_MoA", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID]


def test_two_curators_may_counter_sign_one_judgement(conn, signed_rule):
    """Several signatures per row is the point of detaching them -- a second reviewer
    attesting the same judgement is ordinary clinical governance, and a signature COLUMN
    could not represent it at all."""
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a second curator",
                  registered_by="an operator")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=fingerprint, signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=LATER)
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert sorted(v.holder for v in verdicts) == ["a curator", "a second curator"]
    assert {v.verdict for v in verdicts} == {signing.VALID}


def test_each_signature_is_checked_against_its_OWN_signed_at(conn, signed_rule):
    """Two signatures over one row cover DIFFERENT bytes, because signed_at is inside
    the payload. Rebuilding one payload and reusing it for every signature would fail
    all but the last -- the defect this test exists to catch, since the symptom looks
    exactly like a forgery."""
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a second curator",
                  registered_by="an operator")
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=fingerprint, signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=LATER)
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert all(v.verdict == signing.VALID for v in verdicts)


def test_a_signature_by_an_unregistered_key_reports_unknown_key(conn, signed_rule):
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)          # deliberately NOT registered
    context, payload = signatures.payload_for(
        conn, "curated_interaction", signed_rule["target_id"],
        key_fingerprint=fingerprint, signed_at=LATER)
    signatures.record(
        conn, target_kind="curated_interaction", target_id=signed_rule["target_id"],
        payload_context=context, payload=payload, key_fingerprint=fingerprint,
        signature=signing.sign(private, payload), signed_at=LATER)
    verdicts = {v.key_fingerprint: v.verdict for v in signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])}
    assert verdicts[fingerprint] == signing.UNKNOWN_KEY


def test_a_compromised_key_flags_every_signature_it_made(conn, signed_rule):
    keys.revoke(conn, key_fingerprint=signed_rule["fingerprint"],
                status="compromised", revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.KEY_REVOKED_COMPROMISED]


def test_a_rotated_key_leaves_an_earlier_signature_valid(conn, signed_rule):
    keys.revoke(conn, key_fingerprint=signed_rule["fingerprint"], status="rotated",
                revoked_by="an operator", status_from=LATER)
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdicts = signatures.verify_target(
        conn, "curated_interaction", signed_rule["target_id"])
    assert [v.verdict for v in verdicts] == [signing.VALID]


def test_a_condition_payload_does_not_verify_as_an_interaction(conn, a_contradicted_pair):
    """Spec 4.4's domain separation, exercised end to end rather than on synthetic
    field lists: the two contexts differ, so the bytes differ, so the signature does
    not carry across."""
    private, public = signing.generate_keypair()
    fingerprint = signing.fingerprint(public)
    keys.register(conn, public_key=public, holder="a curator",
                  registered_by="an operator")
    condition_id = curation.record_condition_ruling(
        conn, a_contradicted_pair["moiety"], a_contradicted_pair["condition"],
        "context_dependent", severity="major", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    _, condition_payload = signatures.payload_for(
        conn, "curated_condition", condition_id,
        key_fingerprint=fingerprint, signed_at=SIGNED_AT)
    assert b"curated_condition/v1" in condition_payload
    assert b"curated_interaction/v1" not in condition_payload
```

**The mutation gate — the most important test in this task:**

```python
@pytest.mark.parametrize("field,mutated", [
    ("relationship", "CI_PE"), ("applies", "false"), ("severity", "minor"),
    ("mechanism", "something else"), ("management", "do nothing"),
    ("evidence_grade", "theoretical"), ("source", "SOMEBODY-ELSE"),
    ("reviewed_by", "somebody else"), ("reviewed_against", "MED-RT 2020.01.01"),
    ("subject_moiety_uuid", "00000000-0000-5000-8000-000000000000"),
    ("object_class_uuid", "00000000-0000-5000-8000-000000000001"),
    ("question_uuid", "00000000-0000-5000-8000-000000000002"),
    ("reviewed_at", "2000-01-01T00:00:00.000000Z"),
    ("signer_key_fingerprint", "b" * 64),
    ("signed_at", "2000-01-01T00:00:00.000000Z"),
])
def test_changing_any_signed_field_breaks_the_signature(conn, signed_rule,
                                                        field, mutated):
    """ONE TEST PER SIGNED FIELD, per the standing rule slice 5c.1's PR review produced:
    for every clause in a multi-field guard, name the test that kills its removal.

    A field silently missing from signing.CURATED_INTERACTION_V1 is the ONE defect this
    layer cannot survive -- the signature would keep verifying while the unsigned field
    was free to be anything -- and no aggregate test can see it.

    HOW THE MUTATION IS APPLIED, and why it is not an UPDATE: the curated row is
    append-only, so the row itself cannot be edited. Instead the payload is REBUILT with
    one field replaced and the recorded signature checked against those bytes. That is a
    genuine coverage test rather than a proxy for one: if the field is absent from the
    frozen list, the 'mutated' payload is byte-identical to the original and the
    signature VERIFIES -- failing this test, which is exactly what should happen.
    """
    fields = _payload_fields(conn, signed_rule)          # the row's rendered values
    assert field in dict(fields), (
        f"{field} is not in the payload at all -- either the frozen field list dropped "
        f"it, or this parametrisation names a column that no longer exists")
    rebuilt = signing.canonical_payload(
        "curated_interaction/v1",
        [(name, mutated if name == field else value) for name, value in fields])
    assert rebuilt != signed_rule["payload"], (
        f"changing {field} did not change the payload -- it is not covered by "
        f"signing.CURATED_INTERACTION_V1, so a signature says nothing about it")
    assert signing.verify(signed_rule["public"], rebuilt,
                          signed_rule["signature"]) is False
```

`_payload_fields(conn, signed_rule)` is a module-level helper returning the `(name, rendered_value)` pairs
`payload_for` would build — factor it out of `signatures.payload_for` as a public
`signatures.payload_fields(conn, target_kind, target_id, *, key_fingerprint, signed_at)` so the test uses the
production code path rather than a parallel reimplementation of it.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `signatures.py`**

The three things the implementation must get right, each with the reason:

```python
def payload_fields(conn, target_kind, target_id, *, key_fingerprint, signed_at):
    """The (name, rendered-value) pairs for one target row, in FROZEN order.

    THE TABLE, KEY COLUMN AND CONTEXT COME FROM signature_target_kind -- never from a
    dict in Python. A fourth target kind is then one INSERT in db/030 rather than an
    edit here, in the migration and in a CHECK, which is db/006's lesson.

    The SELECT is composed with psycopg.sql.Identifier over names read from the
    catalogue. They are drugref's own seed values rather than user input, so there was
    never an injection here -- but composition makes that visible at a glance instead of
    requiring the reader to trace where the names came from.
    """
    kind = conn.execute(
        "SELECT target_table, pk_column, payload_context "
        "FROM drugref.signature_target_kind WHERE target_kind = %s",
        (target_kind,)).fetchone()
    if kind is None:
        raise UnknownTargetError(
            f"{target_kind!r} is not a signature target kind. The vocabulary lives in "
            "drugref.signature_target_kind; adding one is an INSERT there.")
    table, pk_column, context = kind
    field_names = signing.FIELD_LISTS[context]
    row_columns = [f for f in field_names if f not in signing.ATTESTATION_FIELDS]
    row = conn.execute(
        sql.SQL("SELECT {cols} FROM drugref.{table} WHERE {pk} = %s").format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in row_columns),
            table=sql.Identifier(table), pk=sql.Identifier(pk_column)),
        (target_id,)).fetchone()
    if row is None:
        raise UnknownTargetError(
            f"drugref.{table} has no row with {pk_column} = {target_id}. Signing a row "
            "that does not exist would produce a payload of NULLs -- a valid signature "
            "over nothing, which looks like a real one.")
    fields = [(name, signing.render(value))
              for name, value in zip(row_columns, row, strict=True)]
    fields.append(("signer_key_fingerprint", key_fingerprint))
    fields.append(("signed_at", signing.render(signed_at)))
    return context, fields
```

`payload_for` wraps it: `context, fields = payload_fields(...)` then
`return context, signing.canonical_payload(context, fields)`.

`record` stores `signing.digest(payload)` as `payload_digest` and asserts nothing about validity — **recording is
not verifying**, and a `record` that refused an invalid signature could not store the evidence that an invalid one
exists.

`verify_target` must **rebuild the payload per signature, at that signature's own `signed_at` and
`key_fingerprint`** (the test above pins this), look the key up via `keys.key_status` and `keys.live`, and return
`signing.verdict(...)` per row, ordered by `signature_id`.

- [ ] **Step 4: Mutation-verify the gate itself**

Temporarily remove `"severity"` from `signing.CURATED_INTERACTION_V1`. Run:

```bash
DRUGREF_TEST_DSN='...' uv run pytest tests/test_signatures_writer.py -k severity -v
DRUGREF_TEST_DSN='...' uv run pytest tests/test_signing_payload_coverage.py -v
```

**Expected: both FAIL** — the first because the mutated payload is byte-identical so verification passes, the
second because the catalog and the frozen list now disagree. Revert. If either passes, that gate is not firing
and must be fixed before Task 8.

- [ ] **Step 5: Full suite, lint, commit**

```bash
DRUGREF_TEST_DSN='...' uv run pytest -q && uv run ruff check .
git add src/drugref/signatures.py tests/test_signatures_writer.py
git commit -m "feat: build, record and verify a per-row curator signature

payload_for reads the table, key column and context from
signature_target_kind rather than a Python dict, so a fourth target kind is
one INSERT in db/030 -- db/006's lesson, applied again.

verify_target rebuilds the payload PER SIGNATURE at that signature's own
signed_at and fingerprint. Two signatures over one row cover different bytes
(signed_at is inside the payload), so a single shared payload would fail all
but the last -- and the symptom looks exactly like a forgery.

record() does not verify. A recorder that refused an invalid signature could
not store the evidence that an invalid one exists, which is precisely what a
node needs to be able to report.

The per-field mutation gate is mutation-verified: with severity removed from
the frozen list, the severity case fails because the mutated payload is
byte-identical. That is the one defect this layer cannot survive and no
aggregate test can see.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `releases.py` — build, publish and verify a release manifest

**Files:**
- Create: `src/drugref/releases.py`
- Test: `tests/test_releases.py`

**Interfaces:**
- Consumes: `signing.canonical_payload`, `render`, `digest`, `sign`, `verify`, `verdict`;
  `signatures.payload_for`, `signatures.record`; `keys.key_status`, `keys.live`.
- Produces: `releases.ManifestEntry` (frozen: `target_kind: str`, `natural_key: str`, `target_id: int`,
  `payload_context: str`, `payload_digest: bytes` — **`natural_key` is what the signed group carries and what
  verification pairs on; `target_id` is an unsigned local convenience pointer**) ·
  `releases.enumerate_live(conn, *, signed_at) -> list[ManifestEntry]` ·
  `releases.manifest_payload(conn, *, release_tag, published_by, published_at, entries, upstream,
  key_fingerprint, signed_at) -> bytes` · `releases.publish(conn, *, release_tag, published_by, private_key,
  key_fingerprint, published_at=None, signed_at=None) -> int` ·
  `releases.ManifestVerdict` (frozen: `release_tag: str`, `signature: str`, `dropped: list`, `added: list`,
  `altered: list`) with a `.is_intact` property · `releases.verify_release(conn, release_tag) -> ManifestVerdict` ·
  `releases.UnknownReleaseError(RuntimeError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_releases.py`. The four verification directions each get their own test — a single
"manifest mismatch" assertion would be over-determined and pass on any one of them:

```python
# tests/test_releases.py
"""Release manifests: build, publish, verify (spec 5.5, 7.2, 8). DB-gated."""
import datetime as dt

import pytest

from drugref import curation, keys, releases, signatures, signing

PUBLISHED_AT = dt.datetime(2026, 8, 9, 6, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def institutional_key(conn):
    private, public = signing.generate_keypair()
    keys.register(conn, public_key=public, holder="drugref.org",
                  registered_by="an operator")
    return {"private": private, "public": public,
            "fingerprint": signing.fingerprint(public)}


@pytest.fixture
def published(conn, institutional_key, a_graded_rule):
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    releases.publish(
        conn, release_tag="2026.08.09", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)
    return "2026.08.09"


def test_a_published_release_verifies_intact(conn, published):
    verdict = releases.verify_release(conn, published)
    assert verdict.signature == signing.VALID
    assert verdict.is_intact
    assert (verdict.dropped, verdict.added, verdict.altered) == ([], [], [])


def test_the_manifest_enumerates_every_live_curated_row(conn, published):
    assert conn.execute(
        "SELECT row_count FROM drugref.release_manifest "
        "WHERE release_tag = %s", (published,)).fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM drugref.release_manifest_entry e "
        "JOIN drugref.release_manifest m USING (manifest_id) "
        "WHERE m.release_tag = %s", (published,)).fetchone()[0] == 1


def test_a_row_the_manifest_lists_but_the_database_lacks_is_a_DROP(conn, published):
    """The direction a transport signature cannot catch at all: a node that received a
    subset. Simulated by deleting the manifest ENTRY's target from the curated table --
    which the floor forbids, so the test TRUNCATEs, the only tool the floor leaves (see
    the PR #72 round's note: a committed row on the append-only floor cannot be
    unpicked with DELETE)."""
    conn.execute("TRUNCATE drugref.curated_interaction CASCADE")
    verdict = releases.verify_release(conn, published)
    assert len(verdict.dropped) == 1
    assert not verdict.is_intact


def test_a_live_row_the_manifest_omits_is_an_ADDITION(conn, published, a_graded_rule):
    """A node carrying a curated judgement drugref never published. Today there is no
    way at all to tell that from drugref's own data."""
    curation.record_condition_ruling(
        conn, a_graded_rule["subject"], _a_condition_uuid(conn), "spurious",
        reviewed_by="somebody", reviewed_against="MED-RT 2026.07.06")
    verdict = releases.verify_release(conn, published)
    assert len(verdict.added) == 1
    assert not verdict.is_intact


def test_a_row_whose_content_changed_is_an_ALTERATION(conn, institutional_key,
                                                      a_graded_rule):
    """The case an operator most wants to catch: a curated row that no longer matches
    what drugref published.

    CONSTRUCTED BY HAND rather than by editing a row, because the floor forbids editing
    either side: the curated table refuses UPDATE, and so does release_manifest_entry.
    So the test INSERTs a manifest whose entry digest for a live row is deliberately
    wrong -- which is byte-for-byte the state a consumer is in when their copy of the
    row was altered, and the only state verification can actually observe.

    The two assertions at the end are the point of the whole release layer: the manifest
    signature is VALID -- it really is drugref's -- while its content claim is FALSE.
    Authenticity and integrity are different questions, and a verifier that collapsed
    them would report a tampered database as a bad signature.
    """
    target_id = curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    wrong = b"\xff" * 32
    natural_key = releases.natural_key_of(conn, "curated_interaction", target_id)
    entries = [releases.ManifestEntry("curated_interaction", natural_key, target_id,
                                      "curated_interaction/v1", wrong)]
    payload = releases.manifest_payload(
        conn, release_tag="2026.08.12", published_by="an operator",
        published_at=PUBLISHED_AT, entries=entries, upstream=[],
        key_fingerprint=institutional_key["fingerprint"], signed_at=PUBLISHED_AT)
    manifest_id = conn.execute(
        "INSERT INTO drugref.release_manifest (release_tag, manifest_digest, "
        "row_count, upstream_releases, published_by, published_at) "
        "VALUES ('2026.08.12', %s, 1, '[]'::jsonb, 'an operator', %s) "
        "RETURNING manifest_id", (signing.digest(payload), PUBLISHED_AT)).fetchone()[0]
    conn.execute(
        "INSERT INTO drugref.release_manifest_entry (manifest_id, target_kind, "
        "natural_key, target_id, payload_context, payload_digest) "
        "VALUES (%s, 'curated_interaction', %s, %s, 'curated_interaction/v1', %s)",
        (manifest_id, natural_key, target_id, wrong))
    signatures.record(
        conn, target_kind="release_manifest", target_id=manifest_id,
        payload_context="release_manifest/v1", payload=payload,
        key_fingerprint=institutional_key["fingerprint"],
        signature=signing.sign(institutional_key["private"], payload),
        signed_at=PUBLISHED_AT)
    verdict = releases.verify_release(conn, "2026.08.12")
    assert verdict.signature == signing.VALID
    assert len(verdict.altered) == 1
    assert (verdict.dropped, verdict.added) == ([], [])
    assert not verdict.is_intact


def test_an_empty_manifest_does_not_verify_a_database_that_has_rows(
        conn, institutional_key, a_graded_rule):
    """THE VACUOUS-PASS TEST. A manifest over zero rows is a MEANINGFUL statement --
    'drugref published nothing' -- not a wildcard, and verifying a database that does
    hold curated rows against it must FAIL with an `added` finding.

    This is exactly the shape this project keeps finding: an empty result that is
    over-determined, and a check that passes because there was nothing to check.
    """
    releases.publish(
        conn, release_tag="2026.08.08", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="major", evidence_grade="established", reviewed_by="a curator",
        reviewed_against="MED-RT 2026.07.06")
    verdict = releases.verify_release(conn, "2026.08.08")
    assert len(verdict.added) == 1
    assert not verdict.is_intact


def test_an_empty_manifest_verifies_an_empty_overlay(conn, institutional_key):
    """The control for the test above. Without it, a verifier that ALWAYS reported an
    addition would pass that test and be useless."""
    releases.publish(
        conn, release_tag="2026.08.07", published_by="an operator",
        private_key=institutional_key["private"],
        key_fingerprint=institutional_key["fingerprint"],
        published_at=PUBLISHED_AT, signed_at=PUBLISHED_AT)
    assert releases.verify_release(conn, "2026.08.07").is_intact


def test_a_correction_is_an_addition_not_an_alteration(conn, published, a_graded_rule):
    """A superseded row leaves the live set and its successor joins it, so a curated
    correction made after publication shows as ONE drop and ONE addition -- which is
    the truth. Reading it as an 'alteration' would imply the published row had been
    edited, and on an append-only floor nothing ever is."""
    curation.record_interaction_judgement(
        conn, a_graded_rule["subject"], a_graded_rule["class"], "CI_MoA", True,
        severity="contraindicated", evidence_grade="established",
        reviewed_by="a curator", reviewed_against="MED-RT 2026.07.06")
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    verdict = releases.verify_release(conn, published)
    assert len(verdict.dropped) == 1 and len(verdict.added) == 1
    assert verdict.altered == []


def test_the_manifest_signature_is_an_ordinary_assertion_signature_row(conn, published):
    """ONE MECHANISM, BOTH LAYERS -- the payoff of detaching the signature from the row.
    A manifest is signed by exactly the same table and the same code path as a curated
    judgement."""
    assert conn.execute(
        "SELECT count(*) FROM drugref.assertion_signature "
        "WHERE target_kind = 'release_manifest'").fetchone()[0] == 1


def test_a_compromised_publishing_key_flags_the_release(conn, published,
                                                        institutional_key):
    keys.revoke(conn, key_fingerprint=institutional_key["fingerprint"],
                status="compromised", revoked_by="an operator",
                status_from=dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc))
    conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    assert releases.verify_release(
        conn, published).signature == signing.KEY_REVOKED_COMPROMISED


def test_an_unknown_release_tag_raises(conn):
    with pytest.raises(releases.UnknownReleaseError):
        releases.verify_release(conn, "no such release")


def test_the_upstream_snapshot_is_recorded(conn, published):
    """`reviewed_against` says which release each JUDGEMENT was formed against; this
    says which releases the DATABASE held at publication. Different questions."""
    upstream = conn.execute(
        "SELECT upstream_releases FROM drugref.release_manifest "
        "WHERE release_tag = %s", (published,)).fetchone()[0]
    assert isinstance(upstream, list)
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `releases.py`**

Key decisions the implementation must encode:

- `enumerate_live` selects live rows from **both** curated tables and computes each digest through
  `signatures.payload_for` — **the same code path a row signature uses**, so a manifest entry and a curator
  signature can never disagree about what a row's canonical bytes are. It passes a **fixed `signed_at` and an
  empty `signer_key_fingerprint`** for entry digests, because a manifest entry attests *content*, not an
  attestation; document that clearly, since it is the one place the two uses of the payload diverge.
- `manifest_payload` builds the `release_manifest/v1` payload: the six scalars plus the `--entries--` and
  `--upstream--` groups, with `entry_count`/`upstream_count` rendered as scalars (spec §5.5). **Each entry
  member carries `target_kind`, `natural_key`, `payload_context`, `payload_digest` — NOT `target_id`**, which is
  database-local and would break verification on any node that rebuilt rather than restored.
- `natural_key_of(conn, target_kind, target_id) -> str` renders a row's own key canonically, via
  `signing.canonical_payload` over that table's key columns read from `signature_target_kind`. One home for the
  rendering, so a manifest written by one drugref and verified by another cannot disagree about it.
- `verify_release` compares manifest entries to `enumerate_live(conn, ...)` **as sets of
  (target_kind, natural_key)** for dropped/added, and compares digests across the intersection to find
  `altered`. Pairing on the natural key rather than on `target_id` is what makes the release layer survive a
  node that rebuilt its database, and what lets `altered` be reported as an alteration rather than as an
  unexplained drop beside an unexplained addition.
- `ManifestVerdict.is_intact` is `self.signature == signing.VALID and not (dropped or added or altered)`.

- [ ] **Step 4: Full suite, lint, commit.**

---

### Task 9: The read path — `db/030` views

**Files:**
- Modify: `db/030_signing.sql` (append section 7)
- Test: `tests/test_signature_read_path.py`

**Interfaces:**
- Produces: views `drugref.curated_signature_status`, `drugref.signature_backdated`; re-issued
  `drugref.curated_ddi_pair` and `drugref.curated_condition_ruling` each with a trailing `signature_status`.

- [ ] **Step 1: Write the failing tests**

The cases: an unsigned curated row reads `unsigned`; a signed one reads `signed`; a row whose only signature is by
a `compromised` key reads `signed_by_revoked_key` **and is still present in the view**; a row with one revoked and
one good signature reads `signed`; `signature_backdated` is empty for a normally-recorded signature and reports one
whose `signed_at` long precedes `recorded_at`; and the existing `curated_ddi_pair` columns are unchanged in order
and name (`CREATE OR REPLACE VIEW` cannot reorder them, and a test asserting the new column is **last** documents
why).

The load-bearing one:

```python
def test_a_row_signed_by_a_compromised_key_is_still_served(conn, ...):
    """SPEC 9's REFUSAL, pinned rather than documented. Gating the read views on a valid
    signature would let a key-management event silently withdraw contraindication advice
    from every downstream consumer -- and FEWER ROWS IS THE HARM DIRECTION for a
    contraindication, which is Plan B's central finding. The row stays; the label
    changes; the consumer decides."""
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Append section 7 to `db/030_signing.sql`**

```sql
-- ============================================================================
-- 7. Read path
-- ============================================================================
-- REGISTRY-LEVEL ONLY. Postgres cannot verify an Ed25519 signature, so this view
-- reports what SQL can know -- is a signature present, is its key registered, has that
-- key been revoked -- and NOT whether the mathematics checks out. `drugref verify` is
-- the only thing that does that.
--
-- NO VERIFICATION RESULT IS EVER CACHED IN A COLUMN. A stored "verified" flag is a
-- claim nothing re-checks, which is the exact failure mode this slice exists to remove.
CREATE OR REPLACE VIEW drugref.curated_signature_status AS
WITH per_target AS (
    SELECT s.target_kind,
           s.target_id,
           count(*) AS signature_count,
           count(*) FILTER (
               WHERE k.key_fingerprint IS NOT NULL
                 AND NOT t.invalidates_all_signatures
                 AND NOT (t.is_revocation AND s.signed_at >= k.status_from)
           ) AS unobjected_count
    FROM   drugref.assertion_signature s
    LEFT   JOIN drugref.signing_key k
           ON  k.key_fingerprint = s.key_fingerprint
           AND k.superseded_by IS NULL
    LEFT   JOIN drugref.signing_key_status_kind t ON t.status = k.status
    GROUP  BY s.target_kind, s.target_id
)
SELECT target_kind,
       target_id,
       signature_count,
       unobjected_count,
       CASE WHEN unobjected_count > 0 THEN 'signed'
            ELSE 'signed_by_revoked_key' END AS signature_status
FROM   per_target;

COMMENT ON VIEW drugref.curated_signature_status IS
    'REGISTRY-LEVEL SIGNATURE STATUS -- NOT CRYPTOGRAPHIC VERIFICATION. Postgres cannot '
    'check an Ed25519 signature; this reports only whether a signature exists, whether '
    'its key is registered, and whether that key has been revoked. `signed` means '
    'NOTHING IN THE REGISTRY OBJECTS, not that the mathematics was checked -- run '
    '`drugref verify` for that. A target with no row here is UNSIGNED, which is an '
    'ordinary state: signing is optional per row.';

-- A row whose signature claims a date long before this database learned of it. An
-- OPERATOR SIGNAL, deliberately not a gap kind -- a curator with an air-gapped signing
-- flow legitimately submits late -- on curated_target_unresolved's precedent. One day
-- is the threshold because `drugref sign` writes within seconds of signing.
CREATE OR REPLACE VIEW drugref.signature_backdated AS
SELECT signature_id, target_kind, target_id, key_fingerprint,
       signed_at, recorded_at, recorded_at - signed_at AS lag
FROM   drugref.assertion_signature
WHERE  signed_at < recorded_at - interval '1 day';

COMMENT ON VIEW drugref.signature_backdated IS
    'Signatures claiming a signed_at more than a day before this database recorded '
    'them. signed_at is INSIDE the signed payload and so cannot be forged by an '
    'attacker without the key -- but a compromised key CAN backdate, which is one '
    'reason a compromise is blanket rather than time-scoped. An operator signal, not a '
    'gap kind: a legitimate air-gapped flow also lands here.';
```

Then re-issue the two read views. Each keeps every existing column **in its existing order** (`CREATE OR REPLACE
VIEW` permits only appending) and gains:

```sql
       COALESCE(ss.signature_status, 'unsigned') AS signature_status
...
LEFT   JOIN drugref.curated_signature_status ss
       ON  ss.target_kind = 'curated_interaction'
       AND ss.target_id   = c.curated_interaction_id
```

**A `LEFT` join, and it must stay left:** an inner join would drop every unsigned curated row from the read path,
which is precisely the silent withdrawal §9 refuses.

- [ ] **Step 4: Re-measure the hot path**

Run `EXPLAIN ANALYZE` on the filtered `curated_ddi_pair` lookup and compare against 5c.1's recorded **2.5 ms**. If
it has moved by more than an order of magnitude, that is a finding to record, not to wave through — the
`assertion_signature` table is empty here, so a large move means the join shape is wrong, not that the data grew.

- [ ] **Step 5: Full suite, lint, commit.**

---

### Task 10: `cli_signing.py` — the whole operator surface

**Files:**
- Create: `src/drugref/cli_signing.py`
- Modify: `src/drugref/cli.py` (one `cli_signing.register(commands)` call in `build_parser`)
- Test: `tests/test_cli_signing.py`

**Commands:** `drugref keys generate | register | revoke | list` · `drugref sign` · `drugref verify` ·
`drugref publish`.

- [ ] **Step 1: Write the failing tests.** Cases, each with its reason:
  - `keys generate` writes the private key **`0600`** and **refuses to overwrite an existing file** — the failure
    mode is silent and unrecoverable, and a curator who overwrites their key loses the ability to be themselves.
  - `keys register` prints the fingerprint it derived, so the operator can read back what they registered.
  - `keys revoke --status nonsense` prints `db.constraint_definition`'s text, not a Python list — the vocabulary
    lives in `signing_key_status_kind`. **No argparse `choices`.**
  - `keys list` prints `none` on an empty registry, matching `drugref status`' three blocks: a bare header reads
    as truncated output rather than as an answer.
  - a blank `--holder` is refused **before any write**, via `cli_policy._reject_blank`'s shape — `required=True`
    checks presence, not content, and the floor makes a blank row uncorrectable.
  - `sign --dry-run` prints the canonical payload and **writes nothing** (spec §4.5's display step).
  - `verify` exits **non-zero only on `bad_signature`** — an `unsigned` row is the ordinary state of the overlay,
    and making it a failing command would make the normal case an error.
  - `main` still does not swallow a `CheckViolation` from an ingest — re-run
    `test_main_does_not_swallow_a_check_violation_from_an_ingest`, since this task adds a second `_write`-style
    caller and the temptation to hoist the catch into `cli.main` returns.

  Model the `committed` fixture on `tests/test_cli_policy.py`'s: **restore by recording a further correction,
  never a `DELETE` or a bare `ROLLBACK`**, because the overlay floor refuses deletion.

- [ ] **Step 2–4: Run to fail, implement, run to pass.** `cli_signing.py` writes **no SQL of its own** — every
  read and write goes through `keys.py`, `signatures.py` or `releases.py`. Add it to
  `tests/test_curation_orphans.py`'s no-SQL grep list at the same time; that guard exists because a
  Python-embedded writer to an append-only curated table is invisible to `pg_rewrite`, and this is a new file full
  of candidates.

- [ ] **Step 5: Check the size cap.** `uv run python -c "print(sum(1 for _ in open('src/drugref/cli_signing.py')))"`
  — if it is over ~500, split the `keys` half from the `sign`/`verify`/`publish` half rather than shipping over the
  cap, since Task 1 exists precisely because that debt was allowed to accumulate once.

- [ ] **Step 6: Full suite, lint, commit.**

---

### Task 11: Measure, publish the decision record, update the state files

- [ ] **Step 1: Build a fresh verification database from the real releases**

Follow PROJECT-NOTES § "How to run / test". **A NEW name** — `drugref_5c4` — never a rebuild over
`drugref_5c1m`, which is the current control and whose ledger must stay intact (PROJECT-NOTES: *a verification
database is never patched; rebuild it under a new name*).

```bash
createdb -h localhost -p 5532 -U postgres drugref_5c4
DSN='host=localhost port=5532 dbname=drugref_5c4 user=postgres'
uv run drugref --dsn "$DSN" migrate
time uv run drugref --dsn "$DSN" ingest chain --downloads downloads \
    --unii-release 26Feb2026 --medrt-release 2026.07.06 \
    --mesh-release 2026 --mesh-relations-release 2026.07.06 \
    --gsrs-release 2026-02-26
```

- [ ] **Step 2: Confirm the counts that must not move**

`ddi_candidate_pair` **21,664** · `substance_moiety` **19,438** · `open_question` **21,842** ·
`gap_uncurated_interaction_rule` **595** · `gap_uncurated_condition_contradiction` **168**. This slice adds no
projection and no gap kind, so **every one of these has no licence to move**. A move is a defect in this branch,
not a new figure to publish.

Also take the **per-leg chain timing** while you are here — issue 81 asks for exactly that, and this is a full
chain run on the same machine. It costs nothing extra and it is the measurement that issue has been waiting for.

- [ ] **Step 3: Exercise the whole surface end to end against that database**

Generate a key, register it, sign one real curated judgement, verify it, publish a release, verify the release,
revoke the key as `compromised`, and verify again — confirming the verdict changes to
`key_revoked_compromised` **and the row is still served** by `curated_ddi_pair`. Record the actual output.

- [ ] **Step 4: Re-measure the hot path** — filtered `curated_ddi_pair` `EXPLAIN ANALYZE` against 5c.1's 2.5 ms.

- [ ] **Step 5: Publish the decision record**

`docs-site/docs/decisions/signing-the-curated-overlay.md`, and add it to the section index. It must state, in the
site's living-record voice: the two layers; curator-held keys and why server-held would prove nothing; the two
kinds of revocation; that signature is metadata and never an admission gate; and — plainly — **what signing does
not fix**, naming issue 2. Then `uv run --group docs mkdocs build --strict -f docs-site/mkdocs.yml`.

- [ ] **Step 6: Update the three state files**

- **`docs/PROJECT-NOTES.md`** — a new § "Slice 5c.4" with the traps a future change can break (the frozen field
  lists and their inverted rule; `is_revocation`; the LEFT join in the read views; `signed` ≠ verified;
  per-signature payload rebuild; the empty manifest). **Also correct the stale test count at § "How to run /
  test"** — it says 958 and was 969 at this branch's start; that file names itself the one home for that number.
- **`docs/ROADMAP.md`** — mark 5c.4 done under § Slice 5c, and **update the execution-order callout**: the
  irreversibility argument is weaker than 5c.1 recorded (a detached signature can be written at any time), so
  5c.4-before-5c.2 is good order rather than a trap. A later round may legitimately reorder them and must know
  the reason changed.
- **`docs/HANDOVER.md`** — regenerate, **within the line bound its own header states** (read it off that file;
  do not copy the number anywhere).

- [ ] **Step 7: Commit, push, open the PR**

PR to `main` describing the two layers, the measurement, and what the slice deliberately does not do. **Link
issue 81** if the per-leg timing from step 2 answers it. Keep every issue number away from `close`/`fix`/`resolve`
in any inflection — that pattern has bitten this repo four times.

---

## Self-Review

Run against the spec with fresh eyes; findings fixed inline above.

**Spec coverage.** §1 → Tasks 1–11 · §2 threat model → the reasons carried in each module docstring · §3 detached
shape → Task 5 · §4.1–4.5 canonical payload → Task 3 · §4.6 Ed25519 → Task 2 · §5.1–5.5 tables → Task 5 · §6 keys
→ Task 6 · §7.1 verdicts → Task 4 · §7.2 bidirectional manifest → Task 8 · §7.3 SQL cannot verify → Task 9 ·
§7.4 what signing does not fix → Task 5's header comment and Task 11's decision record · §8 publish → Task 8 ·
§9 read path → Task 9 · §10.1 `cli.py` split → Task 1 · §10.2–10.3 CLI → Task 10 · §11 licensing → Task 2 ·
§12 tests and measurement → distributed, then Task 11 · §13 non-goals → Task 11's decision record.

**Corrections made during review:**
1. **The task-to-spec mapping in the header was wrong.** It assigned the CLI to Task 6; the CLI became Task 10
   once `keys.py` grew large enough to deserve its own review gate. The mapping line above is the corrected one.
   The **File Structure** table's `cli_signing.py` row should be read as "created in Task 10".
2. **Three transcription hazards were REMOVED from the plan rather than annotated** (pre-flight scan, before
   Task 1 was dispatched). Earlier drafts carried a `make_signing_vectors.build()` stub that raised
   `SystemExit`, a stray `releases.revoke_check = None`, and a comment-bodied
   `test_a_row_whose_content_changed_is_an_ALTERATION` — each with a note telling the implementer not to ship
   it. **A caveat beside a code block is not a safeguard**: an implementer transcribing a task reads the block.
   All three are now written correctly in place, and the ALTERATION test asserts the distinction that motivated
   it — the manifest signature is `VALID` while its content claim is false, because authenticity and integrity
   are different questions.

**Type consistency.** `signing.KeyStatus` is constructed in exactly two places (`keys.key_status`, and the test
constants) and consumed in one (`signing.verdict`) — checked. `signatures.payload_for` returns
`(context, bytes)` while `payload_fields` returns `(context, list[tuple])`; both are named in Task 7's Interfaces
block because the mutation test calls the second directly. `keys.KeyRecord`'s field order matches `_COLUMNS`
exactly, which is what `strict=True` enforces at runtime.

**Known gap, stated rather than hidden.** Tasks 8, 9 and 10 give test *cases with reasons* but not complete test
bodies, where Tasks 1–7 give both. That is a real thinning at the tail. The three tasks are the most mechanical
(one is pure SQL, one is a CLI modelled line-for-line on `cli_policy.py`), and every case names the property it
pins — but an implementer should expect to spend more design effort there, and should push back if a case's reason
does not translate into an obvious assertion.
