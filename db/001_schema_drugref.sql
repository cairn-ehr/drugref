-- db/001_schema_drugref.sql
-- drugref global tier, slice 1: the active-moiety identity spine.
-- Three tables plus an append-only integrity floor enforced IN THE DATABASE, so a
-- buggy ingest -- or a raw-SQL hand -- cannot silently rewrite substance identity.
--
-- Scope note (slice 1): the floor below enforces ROW-LEVEL UPDATE/DELETE immutability
-- via triggers only. TRUNCATE and a table-owning role (ALTER TABLE ... DISABLE TRIGGER,
-- or session_replication_role='replica') are OUT OF SCOPE for this slice and remain
-- bypasses; they close in a later hardening slice via RLS + privilege separation (the
-- full floor design §7 always envisioned). Accepted here because the identity spine is
-- rebuildable reference data, not the signed clinical wire core.

CREATE SCHEMA IF NOT EXISTS drugref;

-- Provenance: every registry/claim row traces to one ingest run, so any state is
-- reproducible and attributable to a specific upstream release.
CREATE TABLE IF NOT EXISTS drugref.ingest_run (
    ingest_run_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source           text        NOT NULL,   -- 'UNII' | 'CHEBI' | ...
    upstream_release text        NOT NULL,   -- the upstream file's release/version tag
    source_checksum  text        NOT NULL,   -- checksum of the ingested file
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz
);

-- The registry: one row per immortal active moiety. moiety_uuid is minted once
-- (UUIDv5 at seed, see src/drugref/ids.py) and NEVER changes.
CREATE TABLE IF NOT EXISTS drugref.substance_moiety (
    moiety_uuid       uuid   PRIMARY KEY,
    display_name      text   NOT NULL,       -- INN-preferred label; a cache derived from claims
    first_seen_ingest bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id)
);

-- External identifiers as append-only CLAIMS that attach to a moiety, never the key
-- (principle 2). A correction OVERLAYS: insert the corrected claim, set superseded_by
-- on the old one. Never UPDATE-in-place, never DELETE.
CREATE TABLE IF NOT EXISTS drugref.identity_claim (
    identity_claim_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    moiety_uuid       uuid        NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    scheme            text        NOT NULL,  -- 'UNII'|'INN'|'RXNORM_IN'|'CHEBI'|'CAS'|'PUBCHEM_CID'|'INCHIKEY'
    value             text        NOT NULL,
    ingest_run        bigint      NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    asserted_at       timestamptz NOT NULL DEFAULT now(),
    superseded_by     bigint      REFERENCES drugref.identity_claim(identity_claim_id),
    -- A claim can never supersede itself -- the overlay path always points to a
    -- DIFFERENT (later) claim row, never back to its own id.
    CONSTRAINT identity_claim_no_self_supersede
        CHECK (superseded_by IS NULL OR superseded_by <> identity_claim_id)
);

-- Idempotent re-ingest: the same (moiety, scheme, value) is one logical claim.
CREATE UNIQUE INDEX IF NOT EXISTS identity_claim_unique
    ON drugref.identity_claim (moiety_uuid, scheme, value);
-- Reverse lookup (value -> moiety), the cross-walk query path.
CREATE INDEX IF NOT EXISTS identity_claim_by_scheme_value
    ON drugref.identity_claim (scheme, value);

-- ---- The append-only floor ------------------------------------------------

-- substance_moiety: forbid DELETE; forbid changing the immortal key. The
-- display_name cache MAY be refreshed by a later ingest.
CREATE OR REPLACE FUNCTION drugref.forbid_moiety_rewrite() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.substance_moiety is append-only: DELETE forbidden';
    END IF;
    IF NEW.moiety_uuid <> OLD.moiety_uuid THEN
        RAISE EXCEPTION 'drugref.substance_moiety.moiety_uuid is immortal: it may not change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER forbid_moiety_rewrite
    BEFORE UPDATE OR DELETE ON drugref.substance_moiety
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_moiety_rewrite();

-- identity_claim: forbid DELETE; the ONLY permitted mutation is setting superseded_by
-- (the overlay/correction path). No other column may change.
CREATE OR REPLACE FUNCTION drugref.forbid_claim_rewrite() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'drugref.identity_claim is append-only: DELETE forbidden';
    END IF;
    IF NEW.moiety_uuid  <> OLD.moiety_uuid
       OR NEW.scheme     <> OLD.scheme
       OR NEW.value      <> OLD.value
       OR NEW.ingest_run <> OLD.ingest_run
       OR NEW.asserted_at <> OLD.asserted_at THEN
        RAISE EXCEPTION 'drugref.identity_claim is append-only: only superseded_by may change';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER forbid_claim_rewrite
    BEFORE UPDATE OR DELETE ON drugref.identity_claim
    FOR EACH ROW EXECUTE FUNCTION drugref.forbid_claim_rewrite();
