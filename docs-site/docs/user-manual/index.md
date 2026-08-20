# Reviewer manual

Drugref Reviewer is the human governance surface for Drugref's curated clinical
knowledge. It is a cross-platform desktop application backed by an authenticated
service—not a general public drug browser and not a direct PostgreSQL client.

The workflow keeps four things deliberately separate:

1. an ingested candidate or published gap;
2. research notes and citation-only evidence;
3. an immutable clinical revision; and
4. a detached signature over that revision's exact content.

Saving one does not silently manufacture the next.

## First run and sign-in

The desktop core asks the service whether an administrator already exists before it
opens a workspace. On a new deployment, only first-administrator registration is
available. The database serialises that bootstrap so concurrent clients cannot create
two first administrators.

After bootstrap, reviewers sign in with their username and password. The resulting
session authorises service actions; it is not a clinical signature. Administrators can
create additional accounts, revise profiles, rotate passwords and revoke all sessions
for an account. The last active administrator cannot be disabled or demoted.

## Work the review queue

The queue is loaded from Drugref's live gap views. Search and the type, source and
relationship filters run on the service, with stable pagination and database-derived
filter values.

Selecting an item opens its canonical review target, provenance, current clinical
history and working record. The first queue categories cover:

- uncurated interaction rules, reviewed once at the rule grain so the decision can
  govern the pairs it expands to; and
- drug–condition contradictions, reviewed at the stable moiety-condition pair because
  indication and contraindication may both be true in different contexts.

Candidates remain advisory. Merely appearing in the queue does not turn source data
into a curated Drugref judgement.

## Record research without making a decision

Reviewers can append Markdown annotations and structured evidence references such as a
DOI, PMID, PMCID, clinical-trial identifier, SPL identifier or URL. These entries are
immutable, reviewer-attributed research history.

They carry citation and context only. They do not encode a verdict, evidence grade,
confidence score, clinical ruling or signature. This lets research accumulate before a
decision is ready without making an unfinished note look authoritative.

## Record a clinical revision

The decision form uses Drugref's controlled clinical vocabularies. Before submission,
the reviewer sees the proposed immutable revision and the current row it will replace.
The service derives reviewer identity and upstream-release provenance itself.

Choose **Record revision** to append the new row. A correction inserts a complete
replacement and supersedes the previous row; it never edits clinical history in place.
If another reviewer changed the target after the form was loaded, stale-write
protection rejects the submission and asks for a refresh.

Recording succeeds before signing. That brief unsigned state is intentional: database
publication and possession of a device-local private key are separate operations.

## Enrol and use a signing key

A reviewer can create an Ed25519 key protected by a separate signing-vault passphrase.
The encrypted private half remains in Tauri Stronghold on that device. Only the public
key and its fingerprint are enrolled with the service; the vault passphrase is never
sent there and is not the account password.

Before signing, the app shows the complete human-readable canonical content in signed
order. Long mechanism and management fields remain visible, every identifier and
provenance field is shown, and SQL `NULL` is explicit. Confirm only after comparing that
content with the decision you intend to attest.

The desktop core signs locally. The service then independently rebuilds the canonical
bytes, verifies the Ed25519 signature against the enrolled public key and inserts the
detached signature. Raw canonical bytes and private key material never enter the
WebView.

## Pending signatures and key replacement

Current revisions without a registry-unobjected signature appear under **Pending
signatures**, including after a queue refresh or application restart. The same complete
confirmation is required whether this is the first signature or an independent
counter-signature.

If the local vault passphrase is lost, use **Replace key**. The service first records a
time-scoped rotation and withdraws that enrolment; only after it commits does the native
client remove the fixed local vault files. Earlier signatures remain in the audit
history and remain acceptable when they predate the rotation boundary.

## Account and public-key administration

Administrators have two distinct governance surfaces:

- **Accounts** — create reviewers, append profile or role corrections, rotate a
  password, disable an account and revoke its sessions.
- **Key trust** — inspect every public fingerprint, reviewer enrolment, status boundary,
  signature count and current-review impact; then append retirement or compromise after
  explicit confirmation.

Retirement is time-scoped: signatures made before the boundary retain their standing.
Compromise permanently objects to every signature from that fingerprint. Affected
current revisions return to Pending signatures until an independent unobjected key
counter-signs them. The compromised signature remains immutable, and clinical rows
remain visible throughout.

The status displayed by database views is registry policy, not a cached cryptographic
verdict. The GUI distinguishes **Signed**, **Unsigned**, **Signed by revoked key** and
**Signed by unknown key**. An unknown fingerprint is the stronger warning when every
signature is objected; one independent unobjected signature still restores **Signed**.
Historical mathematics is checked by `drugref verify`; PostgreSQL does not perform
Ed25519 verification itself.

## Deliberate limits

The reviewer currently has no offline write/synchronisation mode, private-key export or
recovery, automatic clinical re-review, signature quorum requirement, release-manifest
signing UI, or general consumer drug-search surface. Browser preview is representative
layout data only and never substitutes for an authenticated native request.

For the underlying security model, see [Architecture](../architecture/index.md) and
[Signing the curated overlay](../decisions/signing-the-curated-overlay.md).
