# Drugref reviewer service

Authenticated service boundary between the native reviewer application and PostgreSQL.
Apply migrations through `db/045_reviewer_annotations.sql`, then run:

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
new row remains unsigned until the separate local-signing workflow is implemented.

Run the populated-database integration check explicitly:

```sh
DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_db038' \
  cargo test live_queue_query_reads_pages_filters_and_metadata -- --ignored

DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_test' \
  cargo test live_working_record_round_trip -- --ignored

DRUGREF_REVIEW_TEST_DATABASE_URL='postgresql://postgres@localhost:5532/drugref_test' \
  cargo test live_decision_revision_round_trip -- --ignored
```
