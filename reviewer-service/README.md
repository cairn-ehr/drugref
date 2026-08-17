# Drugref reviewer service

Authenticated service boundary between the native reviewer application and PostgreSQL.
Apply `db/044_reviewer_accounts.sql` through `drugref migrate`, then run:

```sh
DATABASE_URL='postgresql://postgres@localhost:5532/drugref_db044' cargo run
```

The service binds to `127.0.0.1:8787` by default. Set `DRUGREF_REVIEW_BIND` when it is
placed behind the production HTTPS reverse proxy. The desktop application reads
`DRUGREF_REVIEW_SERVICE_URL`; release builds require an `https://` URL, while debug
builds also accept loopback HTTP for local development.

On an empty account database, only the bootstrap status and one concurrency-guarded
first-administrator registration are useful. After that registration, the same endpoint
returns a conflict and administrators create users through authenticated `/v1/users`.
