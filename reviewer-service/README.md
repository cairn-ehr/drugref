# Drugref reviewer service

Authenticated service boundary between the native reviewer application and PostgreSQL.
Apply migrations through `db/048_unknown_signature_status.sql`, then run:

```sh
DATABASE_URL='postgresql://postgres@localhost:5532/drugref_reviewer_dev' cargo run
```

Use a dedicated persistent database for the GUI service. **Do not point it at
`drugref_test`**: the PostgreSQL-backed pytest suite recreates that schema and will
erase reviewer accounts and sessions stored there.

The service binds to `127.0.0.1:8787` by default. Set `DRUGREF_REVIEW_BIND` when it is
placed behind the production HTTPS reverse proxy. The desktop application reads
`DRUGREF_REVIEW_SERVICE_URL`; release builds require an `https://` URL, while debug
builds also accept loopback HTTP for local development.

On an empty account database, only the bootstrap status and one concurrency-guarded
first-administrator registration are useful. After that registration, the same endpoint
returns a conflict and administrators create users through authenticated `/v1/users`.
`PUT /v1/users/{reviewer_uuid}/profile` appends a stale-write-guarded complete profile
correction, while the corresponding `/password` route appends an Argon2id credential.
`POST /v1/users/{reviewer_uuid}/sessions/revoke` invalidates every live session. A
password rotation or disablement also revokes affected sessions in the same transaction,
and the last active administrator cannot be disabled or demoted.

Any authenticated reviewer can read `GET /v1/review-queue`. It returns the current gap
totals, database-derived source/relationship filters, stable review targets and a
bounded page of candidates. Supported query parameters are `page`, `pageSize`, `kind`,
`source`, `relationship` and literal substring `search`.

Any authenticated reviewer can also read `GET /v1/review-record` and append through
`POST /v1/review-annotations` or `POST /v1/review-evidence-references`. These routes
write attributed, immutable research history only. They do not update question state,
record an evidence verdict, create a curated assertion or sign anything.

`GET /v1/review-decision` returns immutable curated revision history for one canonical
target. `POST /v1/review-decision` records an interaction judgement or condition ruling
through the existing insert-then-supersede overlay transaction. The service derives
authorship and reviewed releases itself and rejects a stale `expectedRevisionId`; the
new row remains unsigned until the separate local-signing action succeeds.

`GET` and `POST /v1/signing-keys/current` expose the authenticated reviewer's public
key enrolments; private key material never enters this service. `GET
/v1/pending-signatures` lists current GUI revisions awaiting their first signature.
`GET /v1/review-signature` prepares a short-lived canonical challenge and `POST
/v1/review-signature` independently rebuilds its bytes, verifies Ed25519 against an
active enrolled key, and inserts the detached signature.

`DELETE /v1/signing-keys/current` supports lost-passphrase recovery without erasing
audit history. It requires ownership, records the registry key as time-scoped
`rotated`, withdraws the enrolment, and reports how many prior signatures remain
valid. The native client deletes its fixed encrypted vault files only after that
transaction commits and can retry cleanup idempotently.

Administrators can read `GET /v1/signing-keys` to inspect the current public-key trust
projection and append `retired` or `compromised` through `PUT
/v1/signing-keys/{key_fingerprint}/status`. Retirement is time-scoped; compromise
objects to the fingerprint's entire history. Current revisions left with no
registry-unobjected signature return from `GET /v1/pending-signatures` as independent
counter-sign tasks, without withdrawing the clinical row.

Run the populated-database integration check explicitly:

```sh
DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_db038' \
  cargo test live_queue_query_reads_pages_filters_and_metadata -- --ignored

DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_test' \
  cargo test live_working_record_round_trip -- --ignored

DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_test' \
  cargo test live_decision_revision_round_trip -- --ignored

DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_test' \
  cargo test live_detached_signing_round_trip -- --ignored

DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_test' \
  cargo test live_account_administration_round_trip -- --ignored
```
