# drugref — slice 5c.4: signing the curated overlay

**Date:** 2026-08-09 · **Status:** design, approved · **Sequencing:** ROADMAP § 5c's execution-order callout —
**5c.1 ✅ → 5c.4 (this slice) → then 5c.2 / 5c.3 in either order**

The curated overlay has been called *signable* since 5c.1's design round corrected the word, because nothing in
this repository signs anything: no key management, no signing identity, no verification path. This slice builds
that subsystem. It adds **two layers** — a per-row curator attestation and a per-release institutional
attestation — over **one** mechanism, and it ships with **no curated content**, exactly as 5c.1 and Plan C did.

## 1. Scope, and the five things this slice is not

| | |
|---|---|
| **In scope** | key registry + revocation · the canonical payload format · per-row curator signatures · per-release institutional manifests · one verification path over both · the operator CLI |
| Not in scope | any curated content (5c.2's job — this slice makes it *signable*, it does not write it) |
| Not in scope | an air-gapped signing ceremony (§10.3) |
| Not in scope | a transport-level signature over shipped bytes — considered and set aside, §2.3 |
| Not in scope | HTTP distribution (slice 6, still deferred) |
| Not in scope | **issue 2** — the `TRUNCATE` + owner-role bypass. §7.4 states plainly what signing does *not* fix. |

**Why both layers land in one slice.** They share the two expensive parts — the canonical payload format and the
key registry — and the release manifest is signed by the same code path that signs a row. Splitting them means a
second round re-opening the same files, and it would design the canonical form against a single shape before
discovering whether it generalises. The manifest is also what gives the row layer a *consumer* on day one
(`drugref verify`), which is what this project's standing rule about detectors nobody calls demands.

**Why the slice is sequenced here at all.** The append-only floor refuses `UPDATE`. In the shape 5c.1 assumed —
a signature *column* on the curated row — a row committed before signing existed could never be signed, and the
constraint was therefore hard: signing before 5c.2's first row. §3 replaces that shape with a detached table,
which **dissolves** the constraint: a detached signature can be written at any time, including years later.

**The sequencing decision is deliberately kept anyway.** Two reasons. Curators should not accumulate a backlog
of unsigned judgements whose signing then depends on a tool that does not exist; and 5c.2's first content is the
ONC high-priority DDI floor, which is exactly the content whose provenance most wants attesting. Note the change
of *reason*, though: 5c.4 runs before 5c.2 as a matter of good order, no longer as an irreversibility trap. A
future round that needs to reorder them may.

## 2. What the two layers defend against

### 2.1 The row layer — curator non-repudiation

`curated_interaction` and `curated_condition` carry `reviewed_by`, a `text` column that **nothing
authenticates**. It is an attribution in the same sense a filename is: anyone with write access to the database
can type any name into it, and no reader can tell an honest one from an invented one.

A curator signature over the row's own content changes that. The private key never enters drugref
infrastructure, so an insider with total database access can still *write* a row claiming any `reviewed_by`, but
cannot produce a signature over it. The clinical stake is the reason this is worth the friction: curating drug
information for clinical use carries real consequences when done badly, and "who asserted this severity grade,
provably" is a question the tier should be able to answer from any copy of itself, forever.

**The alternatives were considered and rejected.** Server-held per-curator keys deliver only "drugref asserts
that this curator said it", which is what the unauthenticated column already claims — anyone reaching the
keystore signs as anyone. A single institutional key over every row collapses the row layer into the release
layer, so the row signature proves nothing the manifest does not already prove. Both produce a gate that cannot
fire against the threat the layer exists for, and this project has now spent several rounds on checks of exactly
that kind.

### 2.2 The release layer — distribution integrity and completeness

drugref publishes its curated overlay under copyleft, paywall-free. A node operator who loads it has, today, no
way to establish that what they loaded is what drugref published — not at load time, and in particular not
afterwards, which is the state the data spends its entire useful life in.

The release layer signs a **content manifest**: an enumeration of every live curated assertion at publication,
each with its content digest, plus a snapshot of which upstream releases were loaded. Because it enumerates,
verification is bidirectional and catches **omission** as well as alteration — a node missing a row, or carrying
an extra one, both fail.

### 2.3 The option set aside: a signature over shipped bytes

A detached signature over a distribution artefact (a dump, a tarball) in the style of a Debian `Release` file is
simple and format-agnostic, and it was rejected for one reason: **it dies at load time.** Once the bytes are in
a database the signature can never be re-checked against them, so it cannot answer "is this table still what
drugref published?" — which is the question that matters for the following several years. It is cheap to add
later against whatever transport slice 6 produces, and it strands nothing by arriving then.

## 3. The shape: detached signatures, not a column

A signature lives in its own append-only table and points at what it covers. The alternative — a signature
column on the curated row — was rejected on four counts:

1. `db/029` is **merged and therefore frozen**; a column means a new migration regardless.
2. The floor permits no later `UPDATE`, so the signature would have to exist at `INSERT` time. A row written
   before its curator had a key would be **permanently unsigned**.
3. It permits exactly **one** signature per row, so counter-signing — a second reviewer attesting the same
   judgement, which is ordinary clinical governance — is unrepresentable.
4. It does nothing for the release layer, which would then need a second, parallel mechanism.

Detaching answers all four, and its cost is one thing to state honestly: there is a window in which a row exists
unsigned. That is not a new exposure, because signing is optional by design (§9) — an unsigned row reports
`unsigned`, which is the true label.

## 4. The canonical payload — the load-bearing artefact

Everything else in this slice is plumbing. If this is wrong, every signature is worthless.

### 4.1 Requirements

Deterministic; reproducible from a live row years later; **language-independent**, because third parties must be
able to verify without drugref's Python; unambiguous between SQL `NULL` and the empty string; and
**domain-separated**, so bytes signed as one kind of statement cannot be replayed as another.

### 4.2 The form

```
drugref-sig-v1
<context>                                              validated: ^[a-z_]+/v[0-9]+$
<field-count>
<len(name)>:<name>:<tag>:<len(value)>:<value>          one per field, in FROZEN order
...
--<len(group)>:<group>:<member-count>--                zero or more repeated groups
<member-field-count>                                   one block per member; members
<len(name)>:<name>:<tag>:<len(value)>:<value>          sorted by their own COMPLETE
...                                                    encoding, count line included
```

`tag` is `S` for a present value or `N` for SQL `NULL` (length 0, empty value). Lengths are **UTF-8 byte
counts**, so a `management` field containing a newline or a colon cannot forge a field boundary. Line breaks are
decoration for readability; the lengths and counts are what delimit.

**Every structural line is self-delimiting, and that was a correction rather than the first draft.** The
original form applied the length-prefix principle to *values* but not to the format's own structure: the group
header carried a bare name, and a group carried no counts. Three collisions followed, each demonstrated
against the shipped encoder before this text was rewritten:

| two different structures | identical bytes, because |
|---|---|
| `g=[{a:1,b:9},{a:2,b:8}]` vs `g=[{a:1},{b:9,a:2,b:8}]` | no per-member field count, so members ran together |
| `g=[{a:1},{b:2}]` vs `g=[{a:1,b:2}]` | no member count: two members and one merged member are the same concatenation |
| a group named `x--\n--y` vs two empty groups | the group name was not length-prefixed |
| context `evil/v1\n99` | the context was neither validated nor length-prefixed, so it forged the field-count line |

**No forgery followed from any of them** in this codebase — member arity is fixed by the code that builds each
group, and contexts are constants — but a canonical format whose canonicity depends on its callers behaving is
not canonical, and this is a published reference third parties implement against. It was fixed while fixing was
still free: three test vectors existed and nothing had been signed. After the first real signature the format
can never change again.

**THE PAYLOAD IS GENERATE-AND-COMPARE. IT IS NEVER PARSED — by drugref or by anyone.** Verification re-derives
the bytes from the stored row and compares them; it never reads a payload back into fields. The format is
documented so a third party can *reproduce* the bytes from their own copy of the data, which is the only thing
a verifier needs. Worth stating outright because §4.3 calls the format "reimplementable from one paragraph",
and a reader could take that as licence to write a parser and then rely on guarantees a generator does not owe
them.

**Value rendering.** uuid → lowercase canonical 8-4-4-4-12 · boolean → `true` / `false` · `timestamptz` → RFC
3339 in **UTC** with exactly six fractional digits · bigint → plain decimal, no leading zeros · bytea →
lowercase hex · text → **exactly the bytes Postgres returns, with no Unicode normalisation**, so the signature
commits to what is stored rather than to a normalised shadow of it.

**`NULL` and `''` are different bytes on purpose.** `mechanism IS NULL` means "no mechanism recorded" and
`mechanism = ''` means a curator wrote an empty one; 5c.1 already rests on that distinction elsewhere
(`question_uuid` NULL *means* "this grade rests on nothing recorded"), and a format that folded them would let
one be substituted for the other under a valid signature.

### 4.3 Why not RFC 8785 (JSON Canonicalisation Scheme)

JCS is a published standard with test vectors, which is a real advantage — and its genuinely hard part is
**number canonicalisation**, which this payload sidesteps entirely by rendering every value as a string. At that
point JCS contributes only JSON's familiarity while adding an escaping surface to implement correctly. A
length-prefixed form is reimplementable in any language from the paragraph above.

What JCS would have bought — independent checkability — is bought instead by **publishing test vectors**
(§12): a committed fixture of context + field values → hex digest → hex signature, under a clearly-labelled test
keypair. That makes a third-party implementation checkable and makes accidental drift in ours a test failure.

### 4.4 Domain separation and the signer binding

`<context>` is one of `curated_interaction/v1`, `curated_condition/v1`, `release_manifest/v1`. A condition
ruling's bytes can never verify as an interaction judgement.

**Every field list ends with `signer_key_fingerprint` and `signed_at`.** Both are therefore *inside* the signed
bytes: a signature cannot be re-attributed to another key, and cannot be walked across a revocation boundary by
editing a timestamp column. `assertion_signature.recorded_at` is the database's own clock and is deliberately
**not** signed — the gap between a claimed `signed_at` and the `recorded_at` that follows it is a backdating
signal, and gets a view (§7.3).

### 4.5 The field lists are frozen — and the standing rule is inverted deliberately

The gates round's standing rule reads *derive the covered set from the catalog, never from a list you maintain.*
Here the opposite is required: deriving the payload from `information_schema` means a later `ALTER TABLE ADD
COLUMN` silently changes every payload and **invalidates every signature ever made**. So the field lists are
frozen constants in `signing.py`, one per context version.

**The alarm the standing rule exists for is rebuilt rather than abandoned.** A test derives the live column set
from the catalog and asserts it equals the v1 field list ∪ `{<primary key>, superseded_by}`. A new column then
*fails that test*, forcing an explicit decision — bump to a `/v2` context, or exclude the column with a stated
reason — instead of drifting silently. Frozen bytes, catalog-driven alarm.

For `curated_interaction/v1` that list is, in order: `subject_moiety_uuid`, `object_class_uuid`, `relationship`,
`applies`, `severity`, `mechanism`, `management`, `evidence_grade`, `question_uuid`, `source`, `reviewed_by`,
`reviewed_against`, `reviewed_at`, `signer_key_fingerprint`, `signed_at`. For `curated_condition/v1`, the same
with `object_condition_uuid` and `ruling` in place of `object_class_uuid`, `relationship` and `applies`.

**`reviewed_at` stays as `db/029` declared it — `NOT NULL DEFAULT now()` — and `curation.py`'s writers are
unchanged.** An earlier draft of this section required the caller to supply it explicitly, on the grounds that a
curator cannot sign bytes containing a timestamp they never saw. **That requirement was inherited from the
in-row shape §3 rejected and does not survive it.** With a signature *column*, the bytes must be computed
before the `INSERT`, so a DB-generated default is genuinely unsignable. With a detached signature, `drugref
sign` reads the row *after* it exists and signs the value actually stored — so the default is not merely
tolerable, it is the thing being attested.

What the concern does earn is a display step: **`drugref sign` prints the canonical payload before signing**
(and `--dry-run` prints it without writing), so "you can read exactly what you are about to attest" is
satisfied by the tool rather than by breaking a writer's API and every caller of it.

### 4.6 Algorithm

**Ed25519.** 32-byte public keys, 64-byte signatures, no parameter choices to get wrong, and **deterministic** —
no per-signature randomness, so there is no RNG failure mode of the kind that leaks an ECDSA private key.
`algorithm` is stored per key and per signature so a second algorithm is additive rather than a rewrite.

## 5. The tables — `db/030`

### 5.1 `signing_key` — on the overlay floor

Columns: `signing_key_id` (surrogate identity PK), `key_fingerprint` (**the natural key** — SHA-256 of the
public key, lowercase hex), `public_key` bytea, `algorithm`, `holder`, `status`, `status_from` timestamptz,
`registered_by`, `registered_at`, `superseded_by`.

It attaches to Plan C's floor with **no new PL/pgSQL** — `forbid_overlay_rewrite` as `db/020` wrote it,
`forbid_multiple_live_assertions` as `db/023` rewrote it, over a partial `signing_key_live_key` index matching
the trigger's predicate. This is the floor's **eighth** table, and because the gates round rebuilt
`assert_live_key_index` to derive its covered set from `pg_trigger.tgargs`, it is guarded the day the migration
lands, with no list anywhere to update. A test asserts that discovery rather than assuming it.

**Revocation is a correction, not a column edit.** Insert a new row with the new `status`, then point the live
one at it via `overlay.supersede`. The full status history of a key is therefore readable, which is what makes
"was this key already revoked when that signature was made?" answerable at all.

### 5.2 `signing_key_status_kind` — the revocation rule as data

`(status text PRIMARY KEY, is_revocation boolean NOT NULL, invalidates_all_signatures boolean NOT NULL, note
text)`, seeded:

| status | `is_revocation` | `invalidates_all_signatures` | meaning |
|---|---|---|---|
| `active` | false | false | in use |
| `rotated` | true | **false** | superseded by a new key; prior signatures stand |
| `retired` | true | **false** | holder no longer curating; prior signatures stand |
| `compromised` | true | **true** | the private key may be in other hands; every signature it made is suspect |

**Two booleans, not one, and the second is not redundant.** `status_from` on an `active` key is its *registration*
time, and every signature it makes is necessarily after that — so a rule that expired any signature at or after
`status_from` would expire **every** signature ever made. `is_revocation` is what tells the verdict rule that a
`status_from` is an *end* boundary rather than a *start* one. The alternative is a Python-side test for
`status == 'active'`, which would put a member of this vocabulary into a second place, and this project has
spent four rounds on exactly that defect.

`signing_key.status` is a **foreign key into this table**, not a CHECK — `db/006`'s finding, which this project
has now applied four times: a vocabulary with a matching rule in a second place drifts, and the rule here is
precisely the thing a verifier branches on. **`invalidates_all_signatures` has no DEFAULT**, so a fifth status
cannot inherit a guess about whether it destroys evidence. This is the same shape as
`ci_axis.expands_descendants`, `condition_ci_axis`, and `class_expansion_policy` being data a pharmacist can
read.

**Why two kinds of revocation, and not one.** Blanket-only would make the ordinary case destructive: a curator
retires or changes laptop and years of sound clinical judgements silently unsign. Time-scoped-only has no answer
for compromise, where you cannot tell which signatures were the curator's and which the attacker's — the case
the layer exists to survive. Distinguishing them turns a compromise into a **queryable re-review queue** rather
than an undifferentiated failure, and it is the same distinction as `class_expansion_policy`'s `withdrawn` ≠
`allow` and `is_active_component`'s `NULL` ≠ `false`: "no longer current" and "was never valid" are different
statements, and collapsing them has cost this project a round every time.

### 5.3 `assertion_signature` — strictly insert-only

Columns: `signature_id` (surrogate identity PK), `target_kind` (FK into `signature_target_kind`, §5.4),
`target_id` bigint, `payload_context`, `payload_digest` bytea, `key_fingerprint`, `algorithm`, `signature`
bytea, `signed_at` timestamptz, `recorded_at` timestamptz `DEFAULT now()`.

**No `superseded_by`, no `UPDATE`, no `DELETE`, ever.** That is stricter than `forbid_overlay_rewrite` (which
exists to permit exactly one column to change) and needs one small new generic trigger function,
`forbid_any_rewrite()`.

**Why a signature needs no withdrawal path, asked rather than assumed.** This project's standing finding is that
*supersession alone can never withdraw anything*, and four tables have needed a ruling column for it
(`additive_effect.accumulates`, `interaction_group_member.satisfies_role`,
`interaction_group_assertion.applies`, `class_expansion_policy.decision = 'withdrawn'`). The question was put to
this table and the answer is that **retraction happens in the two layers either side of it, never here**:

- A curator who signed a judgement they now disagree with corrects the **judgement** — a new `curated_*` row,
  the predecessor superseded and out of the read path. The old signature remains a true statement about what
  they attested on that date, which is exactly what a row that fired alerts for six months needs.
- A key whose signatures must all be repudiated is handled at the **key** layer, by `compromised`.

A signature is a historical fact about a moment, not an assertion that can be revised. Both ways it could go
wrong already have a home.

`target_id` is the local surrogate PK and is only a **pointer**; it is deliberately not in the signed payload,
because `GENERATED ALWAYS AS IDENTITY` values are local to a database and a rebuild elsewhere would not
reproduce them. Verification re-derives the payload from the row's *content* and checks the signature over that,
so a signature survives being carried into another database whose surrogate keys differ.

### 5.4 `signature_target_kind`

`(target_kind text PRIMARY KEY, target_table text NOT NULL, pk_column text NOT NULL, payload_context text NOT
NULL)`, seeded with the three kinds. One home for the mapping from a `target_kind` to the table, key column and
canonical context it implies, so the resolution is not restated in Python and in SQL.

### 5.5 `release_manifest` and `release_manifest_entry`

`release_manifest`: `manifest_id`, `release_tag` (**UNIQUE** — drugref's own version string, a concept that does
not exist in the repo today), `manifest_digest` bytea, `row_count` integer, `upstream_releases` jsonb (a
snapshot of `loaded_release` at publication), `published_by`, `published_at`. Insert-only, same trigger as
§5.3.

`release_manifest_entry`: `(manifest_id, target_kind, natural_key)` primary key, plus `target_id`,
`payload_context` and `payload_digest`. Insert-only.

The manifest's own signature is an `assertion_signature` row with `target_kind = 'release_manifest'`, signed by
the institutional key. **One mechanism carries both layers** — the payoff of §3, and the reason the two halves
belong in one slice.

The manifest's canonical payload is the §4.2 form with two repeated groups: `--entries--` (each entry's
`target_kind`, **`natural_key`**, `payload_context`, `payload_digest`) and `--upstream--` (each loaded
release's `source`, `writer`, `release`). Members are **sorted by their own encoding**, so the manifest body
does not depend on the order rows came back in.

**A MANIFEST ENTRY IS KEYED ON THE NATURAL KEY, NEVER ON `target_id`, and the first draft of this section had
that wrong.** It listed `target_id` in the signed group while §5.3 twenty lines earlier explains that
`target_id` is a database-local `GENERATED ALWAYS AS IDENTITY` value, deliberately kept out of a signed payload
so a signature survives being carried into another database. The two statements cannot both stand, and the
consequence of the wrong one is precise: **a node that rebuilt rather than restored would assign different
identity values, and every entry would fail to match — the release layer would be broken in exactly the
situation it exists for.**

`natural_key` is the canonical rendering of the row's own key — `subject_moiety_uuid`, `object_class_uuid` and
`relationship` for a curated interaction; `subject_moiety_uuid` and `object_condition_uuid` for a curated
condition — and it is stable across databases because `moiety_uuid` is immortal and `class_uuid`/`condition_uuid`
are deterministic UUIDv5 mints. That is what makes **`altered` nameable at all**: pairing on the natural key
lets verification say "this row's content changed", where pairing on the digest alone could only ever report
one `dropped` plus one `added` and leave a consumer to guess whether they were the same row.

`target_id` stays as an **unsigned** column, purely so an operator can join an entry back to the local row it
describes. It is a convenience pointer, and nothing verifies against it.

**Each group's cardinality is also a signed scalar field** — `entry_count` and `upstream_count` — derivable
from the group itself and stated anyway, on purpose: a group truncated at its end is otherwise detectable only
by recomputing the whole digest, and a scalar count makes the specific failure nameable. `row_count` on
`release_manifest` is `entry_count` as a stored column.

## 6. Keys — registration, rotation, and what a fingerprint is

`key_fingerprint` is SHA-256 over the 32 raw public-key bytes, lowercase hex, and it is **the identity a
signature names**. Public keys are registered by an operator (`drugref keys register`) out of band — this slice
does **not** build an enrolment protocol, a web of trust, or a certificate chain, and the trust root is
therefore "an operator with database access decided this public key belongs to this holder". That is a real
limitation and §13 records it as such.

`holder` is free text and is expected to match the `reviewed_by` a curator writes, but **nothing enforces the
match**, deliberately: enforcing it would put the same string in two places under a constraint that a legitimate
name change breaks. A verifier reports both, and a mismatch is a fact a consumer can act on.

## 7. Verification

### 7.1 Verdicts, and a declared precedence

`no_signature` · `unknown_key` · `bad_signature` · `key_revoked_compromised` · `key_expired` · `valid`.

Evaluated in that order, and **the order is load-bearing**: you cannot check the mathematics without the public
key, so an unknown fingerprint is not a "bad signature", and conflating them reports a routine registry gap as
an attack. `key_expired` means the signature's `signed_at` is at or after the `status_from` of a **time-scoped**
revocation (§5.2); `key_revoked_compromised` applies regardless of `signed_at`, which is the whole content of
`invalidates_all_signatures`.

The verdict rule is a **pure function** in `signing.py`, on `accumulation.fires`' precedent — drugref publishes
facts rather than verdicts, and hands out the rule as code so that "why did this verify?" has one answer
everywhere.

### 7.2 Manifest verification is bidirectional

`verify_manifest` checks the institutional signature over the manifest body, then compares the manifest's
entries against the live set of live curated rows **in both directions**, reporting: entries in the manifest
with no matching row (**dropped**), live rows absent from the manifest (**added**), and matching rows whose
recomputed content digest differs (**altered**). Three distinct findings, because one undifferentiated
"mismatch" would be over-determined and pass on any single one of them.

**An empty manifest is a meaningful statement, not a wildcard.** A manifest over zero rows says "drugref
published nothing", and verifying a database that *does* hold curated rows against it must **fail** with an
`added` finding. This is exactly the vacuous-pass shape this project keeps finding, and it gets its own test.

### 7.3 What SQL cannot do

**Postgres cannot verify an Ed25519 signature.** The `signature_status` column §9 adds therefore reports
**registry-level facts only** — is a signature present, is its key known, has that key been revoked — and *not*
cryptographic validity. `drugref verify` is the only thing that checks the mathematics.

The `COMMENT ON` says so in terms that cannot be misread, because a column called `signature_status` reading
`signed` will otherwise be read as "verified" by exactly one future consumer. **No verification result is ever
cached in a column**: a stored "verified" flag is a claim nothing re-checks, which is the failure mode this
whole slice exists to remove.

`signature_backdated` is a small view over the gap between a claimed `signed_at` and the `recorded_at` that
followed it — an operator signal, deliberately not a gap kind, on `curated_target_unresolved`'s precedent.

### 7.4 What signing does not fix — stated plainly

An attacker with full database write access **cannot** forge a signed judgement attributed to a curator, alter a
signed row undetectably, backdate a signature, or silently drop a row from a published release. They **can**
insert unsigned curated rows (which read `unsigned` — the honest label), and, as a superuser, drop the
append-only triggers outright, which is [issue 2](https://github.com/cairn-ehr/drugref/issues/2)'s `TRUNCATE` +
owner-role bypass and is **not closed by this slice**.

Signing converts *trust the database* into *trust the key holders*. That is a real reduction and it is not the
same as making the database tamper-proof. The decision record says this rather than letting the word "signed"
do work it cannot.

## 8. The release manifest as an operation

`drugref publish --release-tag <tag> --key <file> --published-by <name>` enumerates every **live** curated row
across both curated tables, computes each one's canonical payload digest, snapshots `loaded_release`, writes the
manifest and its entries, then signs the manifest body with the institutional key.

`release_tag` is **stated by the operator, never derived** — the same discipline as `ingest_run`'s release tags,
which PROJECT-NOTES records as "stated, never parsed from a filename". Uniqueness is enforced, so a tag cannot
be reused for a second manifest.

## 9. Read path

`curated_ddi_pair` and `curated_condition_ruling` gain a **trailing** `signature_status` column via `CREATE OR
REPLACE VIEW` — `db/029` is frozen, and appending a column is what `CREATE OR REPLACE` permits. Values, by
declared precedence when a row carries several signatures:

| value | means |
|---|---|
| `signed` | at least one signature whose key is registered and against which SQL can see **no registry-level objection**: its key's live status is not `invalidates_all_signatures`, and `signed_at` precedes that status's `status_from` |
| `signed_by_revoked_key` | signatures exist, but every one of them fails one of those two registry tests |
| `unsigned` | no signature row at all |

`signed` therefore means *"nothing in the registry objects"*, **not** *"the mathematics was checked"* — §7.3.

**A signature is not an admission gate**, and that is a deliberate refusal. Gating the read views on a valid
signature would make the entire curated tier invisible until curators are signing, and — far worse — a key
revocation would silently withdraw contraindication advice from every downstream consumer. **Fewer rows is the
harm direction for a contraindication**; that is Plan B's central finding, and a key-management event must not
be able to trigger it. Publishing the fact and letting the consumer set policy is the same posture as
`is_direct`.

A companion view at the **curated-row grain** carries the per-row signature counts for anyone who needs the
detail, keeping the pair-grain hot path to a single cheap join. The filtered `curated_ddi_pair` lookup was
measured at 2.5 ms in 5c.1 and is re-measured here, since it gains that join.

## 10. Operator surface

### 10.1 `cli.py` is split first

`cli.py` is **508 lines, already over CLAUDE.md's ~500 cap**, and PROJECT-NOTES records the remedy as a
prerequisite: *"Splitting it is the next change to that file, before any new handler."* This slice adds seven.
The split follows the seam PROJECT-NOTES already names — the DB-free argument layer (`STEPS`, `build_parser`,
`resolve_inputs`, `selected_steps`, `main`) stays in `cli.py`; the `_handle_*` entry points, which already take
a connection and are deliberately thin, move to `cli_handlers.py`. This is task 1, not a cleanup at the end.

### 10.2 The commands

`drugref keys generate | register | revoke | list` · `drugref sign` · `drugref verify` · `drugref publish`, all
in a new `cli_signing.py` (like `cli.py` and `cli_policy.py`, it writes no SQL of its own).

`keys revoke --reason` takes **no argparse `choices`**: the vocabulary lives in `signing_key_status_kind` and
the rejection message quotes it, per the standing rule that a vocabulary written down twice is two things that
can disagree — `cli_policy`'s `--decision` is the precedent, including its `db.constraint_definition` shape.

`keys generate` writes the private key `0600` and **refuses to overwrite an existing file**, because the failure
mode is silent and unrecoverable.

### 10.3 Signing runs on the curator's machine

`drugref sign` reads the target row from the database, builds the canonical payload, signs it with a local key
file, and writes the `assertion_signature` row. The private key never touches drugref's infrastructure.

The stronger alternative — export canonical bytes, sign on an offline box, import the signature — is genuine
air-gap discipline and is a great deal of ceremony for a per-row operation. It is **not built here and is not
foreclosed**: the canonical payload is a published, reproducible artefact, so an air-gapped flow is additive
whenever it is wanted.

## 11. Licensing — rule 6

There is no Ed25519 implementation in the Python standard library, so a dependency is unavoidable. This is the
first new runtime dependency since the project began (`psycopg[binary]` was the only one).

**`cryptography` (PyCA) is `Apache-2.0 OR BSD-3-Clause`** — verified against PyPI metadata for 50.0.0, which
declares the license expression `"Apache-2.0 OR BSD-3-Clause"` and ships both `LICENSE.APACHE` and
`LICENSE.BSD`. Dual licensing lets drugref **elect BSD-3-Clause**, which is AGPL-3.0-compatible without relying
even on Apache-2.0's one-way compatibility with GPLv3/AGPLv3. Rule 6 clears.

`NOTICE` is **unchanged**: it attributes bundled *reference-data sources*, and this is a code dependency that
redistributes no data. The licence determination is recorded here and in the decision record.

## 12. What ships, and what a test must prove

**Ships:** `db/030` (six tables — two of them seeded vocabularies — the `forbid_any_rewrite` trigger, the two
re-issued read views, the row-grain companion view, the backdating view); `signing.py` (pure); `keys.py`,
`signatures.py`, `releases.py`;
`cli_signing.py`; the `cli.py` → `cli_handlers.py` split; the `cryptography` dependency; committed test vectors;
a published decision record.

**Does not ship:** any curated row, any registered production key, any air-gapped flow, any transport signature.

The gates that would otherwise not fire — this project's recurring defect, and each of these is named because
its absence is what a green suite would look like:

1. **Test vectors**, committed, under a clearly-labelled test keypair that is never registered anywhere real:
   context + field values → hex digest → hex signature. Makes the format independently reimplementable, and
   makes silent drift in our own encoder a failure.
2. **One mutation test per signed field.** Change that field in the row; assert verification now fails.
   Per 5c.1's PR-review rule — *for every clause in a multi-field guard, name the test that kills its removal,
   one per clause*. A field quietly missing from the frozen list is the one defect this layer cannot survive,
   and no aggregate test sees it.
3. **The catalog alarm** (§4.5): live columns == frozen field list ∪ `{<pk>, superseded_by}`, per curated table.
4. **Domain separation**: a `curated_condition` payload must not verify under the `curated_interaction` context,
   nor a row payload under `release_manifest/v1`.
5. **`NULL` ≠ `''`**: two rows differing only in that must produce different digests.
6. **The manifest catches all three directions, one test each** — dropped, added, altered. A single
   "manifest mismatch" assertion is over-determined and would pass on any one of them.
7. **The empty manifest does not vacuously pass** (§7.2).
8. **Revocation semantics both ways**: `rotated` leaves prior signatures `valid` and makes later ones
   `key_expired`; `compromised` flags every signature that key ever made, regardless of `signed_at`.
9. **Verdict precedence** (§7.1), one case per boundary — in particular that an unknown key does not report
   `bad_signature`.
10. **The floor on each new table, per its own kind.** `signing_key` takes the overlay floor — `DELETE` refused,
    `UPDATE` refused except of `superseded_by`, supersession one-way. The three insert-only tables
    (`assertion_signature`, `release_manifest`, `release_manifest_entry`) refuse `DELETE` **and** every
    `UPDATE`, including of a single column. The two seeded vocabularies (`signing_key_status_kind`,
    `signature_target_kind`) carry no floor, exactly as `ci_axis` does not.
11. **`signing_key` is discovered as the eighth live-key table** by `assert_live_key_index`'s `pg_trigger.tgargs`
    derivation, with no list edited anywhere.
12. **The read-view column is registry-level, and says so**: a row signed by a key that is revoked
    `compromised` still appears in `curated_ddi_pair`, with `signature_status = 'signed_by_revoked_key'` — the
    §9 refusal, pinned rather than documented.

**Measurement**, on a fresh database built from the real releases: `ddi_candidate_pair` **21,664**,
`substance_moiety` **19,438**, `open_question` **21,842** unmoved; the filtered `curated_ddi_pair` lookup
re-timed against 5c.1's recorded **2.5 ms**; and a signed round trip end to end through the CLI.

## 13. What this slice does not answer

- **No enrolment protocol and no trust root beyond an operator.** A public key is trusted because someone with
  database access registered it. A certificate chain, a web of trust, or an organisational root key are all
  additive later; none is needed for the threat in §2.
- **No threshold or quorum signing.** Counter-signing is *representable* (several signatures per row) but
  nothing requires or interprets N-of-M.
- **Issue 2 remains open** (§7.4) and is arguably more visible now, since a superuser dropping a trigger is the
  remaining way to remove a signature.
- **Key custody policy is out of scope** — how a curator stores, backs up and protects a key file is
  governance, not schema. The one mechanical consequence is recorded: losing a key file means registering a new
  one, and prior signatures survive.
- **`upstream_releases` is a snapshot, not a constraint.** The manifest records which releases were loaded at
  publication; it does not verify that a consumer's database loaded the same ones.

## 14. Implementation order

1. **Split `cli.py`** into `cli.py` + `cli_handlers.py` (§10.1). No behaviour change; the suite is the gate.
2. **Add `cryptography`**, elected BSD-3-Clause, with the rule-6 determination recorded.
3. **`signing.py`** — pure canonical encoder, frozen field lists, Ed25519 primitives, fingerprints, the verdict
   rule. Test vectors written here, before anything reads a database.
4. **`db/030`** — the six tables, two seeded vocabularies, `forbid_any_rewrite`.
5. **`keys.py`** + `drugref keys generate|register|revoke|list`.
6. **`signatures.py`** + `drugref sign` + `drugref verify` for a single target.
7. **`releases.py`** + `drugref publish` + `drugref verify --release`.
8. **The read views** re-issued with `signature_status`, plus the row-grain companion and the backdating view.
9. **Measure** against the real releases (§12), then the decision record and the state-file wrap-up.
