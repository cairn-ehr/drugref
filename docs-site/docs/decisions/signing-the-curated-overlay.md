# Signing the curated overlay

**Status:** Active
**Last reviewed:** 2026-08-10
**Applies to:** slice 5c.4 — `db/030`, `signing.py`, `keys.py`, `signatures.py`, `releases.py`,
`release_verification.py`, and the `drugref keys | sign | verify | publish` commands
**Full derivation:** the [slice-5c.4 signing design spec](https://github.com/cairn-ehr/drugref/blob/main/docs/superpowers/specs/2026-08-09-drugref-slice-5c4-signing-design.md)

## Context

drugref's curated overlay carries clinical judgements — a severity grade on a drug–drug rule, a ruling on a
drug–condition pair — written by named people. Every curated table has a `reviewed_by` column, and **nothing
authenticates it**. It is an attribution in the same sense a filename is: anyone with write access can type
any name into it, and no reader of any copy of the database can tell an honest one from an invented one.

Two different readers need two different assurances, and one mechanism does not serve both.

A clinician's software, reading a `contraindicated` grade, wants to know **who asserted it, provably** — a
question that must stay answerable from any copy of the database, for years, long after the transport that
delivered it is gone.

A node operator who has just loaded drugref's copyleft, paywall-free release wants to know **that what they
loaded is what drugref published, whole** — not only at load time but afterwards, which is the state the data
spends its entire useful life in.

## Decision

**Two layers of detached Ed25519 signature, sharing one canonical payload format and one key registry.**

### The row layer — curator non-repudiation

A curator signs one curated row's own content with a private key **held by the curator, never by drugref**.
The signature lands in `assertion_signature`, a strictly insert-only table that points at the row it covers.

Server-held keys were rejected, and the reason is the whole point of the layer. A per-curator key sitting in
drugref's own keystore delivers only *"drugref asserts that this curator said it"* — precisely what the
unauthenticated column already claims, since anyone reaching the keystore signs as anyone. A single
institutional key over every row collapses the row layer into the release layer, leaving the row signature
proving nothing the manifest does not already prove. Both build a gate that cannot fire against the threat the
layer exists for.

Because the key is the curator's, an insider with total database access can still *write* a row claiming any
`reviewed_by` — but cannot produce a signature over it.

### The release layer — distribution integrity and completeness

`drugref publish` enumerates every live curated assertion, records each one's content digest in
`release_manifest_entry` alongside a snapshot of which upstream releases were loaded, and signs the manifest
with an institutional key.

Because it **enumerates** rather than digesting a blob, verification runs in both directions and names three
distinct findings: entries in the manifest with no matching row (**dropped**), live rows absent from the
manifest (**added**), and matching rows whose recomputed digest differs (**altered**). Omission is caught, not
just alteration. One undifferentiated "mismatch" would be over-determined and would pass on any single one of
them.

An empty manifest is a meaningful statement, not a wildcard: it says drugref published nothing, and verifying
a database that *does* hold curated rows against it fails with an `added` finding.

### Signatures are detached, not a column

A signature column on the curated row was rejected on four counts: `db/029` is merged and frozen; the
append-only floor permits no later `UPDATE`, so a row written before its curator had a key would be
*permanently* unsigned; a column permits exactly one signature, making counter-signing — ordinary clinical
governance — unrepresentable; and it does nothing for the release layer.

### Two kinds of revocation, and the difference is data

`signing_key_status_kind` stores the revocation rule as rows rather than as branches in code:

| status | `is_revocation` | `invalidates_all_signatures` | effect |
|---|---|---|---|
| `active` | no | no | in use; `status_from` is the registration time, not an expiry |
| `rotated` | yes | no | **time-scoped** — signatures made *before* `status_from` still verify |
| `retired` | yes | no | **time-scoped** — a curator leaving does not unsound their past judgements |
| `compromised` | yes | **yes** | **blanket** — every signature this key ever made is suspect, regardless of `signed_at` |

The distinction is the substance of the layer. A new laptop or a scheduled rotation must not invalidate a
curator's prior clinical work; a leaked private key must, because after a compromise there is no way to tell
the holder's signatures from the attacker's.

A blanket revocation is **permanent**, and that is enforced by reading a key's whole history rather than its
current row. Both the verifier (`keys.key_status`) and the read view ask *has this fingerprint ever carried a
status with `invalidates_all_signatures`?* — because `keys revoke` writes whatever status it is handed, so
resolving from the live row alone made `keys revoke --status active` on a compromised key silently return every
signature it ever made, **including the thief's**, to `valid`. A time-scoped revocation stays reversible: a
mistaken `rotated` must be correctable on an append-only floor, and a new laptop must not unsound past work.

Revocation is itself a **correction, never a column edit**: `keys revoke` inserts a new `signing_key` row and
points the old one at it, so the registry's own history stays readable — which is what the permanence rule above
actually reads. The verdict rule is a pure function in
`signing.py`, on `accumulation.fires`' precedent — drugref publishes facts rather than verdicts, and hands out
the rule as code so "why did this verify?" has one answer everywhere.

### A signature is metadata, never an admission gate

`curated_ddi_pair` and `curated_condition_ruling` carry a trailing `signature_status` column —
`signed` · `signed_by_revoked_key` · `unsigned` — and **no row is ever withheld because of it**.

That is a deliberate refusal, and the reason is clinical. Gating the read views on a valid signature would
make the entire curated tier invisible until curators are signing, and — far worse — a key revocation would
silently withdraw contraindication advice from every downstream consumer. **Fewer rows is the harm direction
for a contraindication.** A key-management event must not be able to cause it. drugref publishes the fact and
lets the consumer set policy, the same posture as `is_direct`.

`signed_by_revoked_key` is the coarser of two SQL labels and covers a key the registry has **never heard of**
as well as a revoked one — an unknown key being the *more* suspicious of the two. Telling them apart is
`drugref verify`'s job; whether the view should carry a third value is
[issue 86](https://github.com/cairn-ehr/drugref/issues/86).

**`signed` does not mean verified.** Postgres cannot verify an Ed25519 signature, so `signature_status`
reports registry-level facts only: is a signature present, is its key known, has that key been revoked. Only
`drugref verify` checks the mathematics, and **no verification result is ever cached in a column** — a stored
"verified" flag is a claim nothing re-checks, which is the failure mode this whole slice exists to remove.

## What signing does not fix

Stated plainly, because the word "signed" will otherwise do work it cannot.

An attacker with full database write access **cannot** forge a signed judgement attributed to a curator, alter
a signed row undetectably, backdate a signature, or silently drop a row from a published release.

They **can**:

- **Insert unsigned curated rows.** These read `unsigned`, which is the honest label — but a consumer that
  ignores `signature_status` gains nothing from this slice.
- **As a superuser, drop the append-only triggers outright.** This is
  [issue 2](https://github.com/cairn-ehr/drugref/issues/2)'s `TRUNCATE` + owner-role bypass, and it is **not
  closed here**. It is arguably *more* visible now, because dropping a trigger is the remaining way to remove
  a signature. Verification against a signed release still catches the resulting content drift on any node
  that runs it — but the local database's own floor is not what stops a superuser.
- **Disarm every compromise verdict with one `UPDATE`.** `signing_key_status_kind` carries the revocation
  rule as data — which is the point — but, unlike `signing_key` and `assertion_signature`, it carries **no
  append-only floor**, so `UPDATE signing_key_status_kind SET invalidates_all_signatures = false WHERE
  status = 'compromised'` silently turns the blanket revocation above into a time-scoped one on that node.
  Flooring it is purely additive later (a trigger, not a column) and is tracked as
  [issue 85](https://github.com/cairn-ehr/drugref/issues/85). **A second route to the same outcome — needing no
  raw SQL at all, just `drugref keys revoke --status active` on a compromised key — was found in review and is
  CLOSED**: permanence is now read off the key's whole history, not its live row. Issue 85 covers the remaining
  one. Note the floor belongs on that table **only**:
  its sibling `signature_target_kind` is *designed* to be updated, since moving a target kind to a `/v2`
  payload context is exactly the migration the read-back machinery exists to support.

Signing converts **trust the database** into **trust the key holders**. That is a real reduction in what a
consumer must take on faith, and it is **not** the same as making the database tamper-proof.

Three further limits are deliberate rather than unfinished: there is **no enrolment protocol** — a key is
trusted because an operator with database access registered it, and a certificate chain or organisational root
is additive later; there is **no threshold or quorum rule** — counter-signing is representable, but nothing
requires or interprets N-of-M; and `upstream_releases` is a **snapshot, not a constraint** — the manifest
records which releases were loaded at publication, it does not verify that a consumer loaded the same ones.

## Consequences

- **A row exists unsigned for a window**, which detaching makes unavoidable. This is not a new exposure:
  signing is optional by design, and an unsigned row reports `unsigned`.
- **Counter-signing works.** Several signatures per row are representable, and the read view resolves them by
  declared precedence — one good signature outweighs one made with a since-compromised key.
- **Losing a key file is recoverable and prior signatures survive**: register a new key. Key custody is
  governance, not schema.
- **The hot path did not regress.** The filtered `curated_ddi_pair` lookup, measured at 2.5 ms in 5c.1, runs
  at **~1.4 ms** with the new signature join executing against a populated, signed overlay (~1.3 ms with an
  empty one) — measured on a fresh database built from the real 2026 upstream releases.
- **A signature that cannot be rebuilt is a verdict, never a crash.** Every `payload_context` column carries a
  shape check and deliberately no foreign key — a signature records the context it was *actually* signed under,
  and a future `/v2` must not retroactively invalidate every `/v1` signature on file. The cost is that the column
  accepts a context no field list knows, or one belonging to another target kind; since these tables are
  insert-only, a verifier that raised on one would have had verification of that row **denied permanently**, which
  is a cheaper attack than forging anything. Such a signature reports `bad_signature`, and the honest signatures
  beside it still report `valid`.
- **The frozen column lists, and the standing rule inverted deliberately.** Everywhere else in drugref, a
  column list is derived so it cannot drift. **Two** lists here are written down instead — the payload's
  fields, and the columns that render a manifest entry's `natural_key` — because both enter signed bytes,
  and a derived list would silently change what was signed the moment a migration touched the table. The
  natural key is the subtler of the two and was caught in final review: it is a *rendered string* recorded
  at publication and also the key verification **pairs** on, so deriving its columns from today's schema
  compared a past recording against a present shape. An additive migration widening a curated table's key
  would have re-keyed every live row and reported 100% churn on a database nobody had touched.
  <br>Both lists keep the alarm the derive-from-the-catalog rule exists for: a test compares each against
  the live catalog and fails on any divergence, forcing a deliberate `/v2` rather than a silent rewrite.
  The same reasoning makes verification read `payload_context` and `algorithm` **back from the recorded
  row** rather than re-deriving them: verification reconstructs the past, it does not re-describe the
  present.

## Related

- [The hybrid store](hybrid-store.md) — why the curated overlay is the signable half, and the projections
  are not.
- [Curating a drug–condition pair](curating-a-drug-condition-pair.md) — the overlay this slice makes signable,
  and its "signable rather than signed" note, now delivered.
- [Append-only claims](append-only-claims.md) — the correction mechanism `keys revoke` reuses.
- [A curated correction needs a deferred check, not a unique index](correcting-a-curated-assertion.md) — the
  floor `signing_key` sits on.
