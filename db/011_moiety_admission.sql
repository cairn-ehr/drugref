-- db/011_moiety_admission.sql
-- Why each moiety is in the registry: the evidence behind the membership gate.
--
-- Slice 1 defined membership as `has_inn = bool(INN_ID)` on the assumption that
-- UNII's INN_ID column means "this substance has a WHO INN". Measured against the
-- real UNII_Records_26Feb2026.txt (168,046 rows) that is false: INN_ID is
-- populated for 7.49% of records and is EMPTY for amoxicillin, morphine, codeine,
-- doxycycline, tacrolimus, dasatinib and aspirin. It is a sparse cross-reference,
-- not a has-INN flag. Issue #26 replaced the gate with
--
--     INN_ID | USAN_ID | (RXCUI & drug-like SUBSTANCE_TYPE) | legacy allow-list
--
-- and once the gate has four possible reasons, "is this substance a drug?" stops
-- having a single answer and starts having an ARGUMENT. This table records it.
--
-- WHY A TABLE AND NOT A COLUMN ON substance_moiety:
--   * The evidence is SET-VALUED. Paracetamol is admitted by an INN_ID and an
--     RxCUI; recording only the strongest would make "rests on one weak signal"
--     indistinguishable from "corroborated twice", which is the question the
--     table exists to answer.
--   * substance_moiety is floor-protected (db/001) and holds only immortal facts.
--     Admission evidence is neither immortal nor a fact about identity.
--
-- WHY NO APPEND-ONLY FLOOR HERE (contrast db/001, compare db/002 and db/009):
-- this is a REBUILDABLE PROJECTION of one release's observations. The moiety is
-- immortal -- its UUID is published and consumers cite it -- but the evidence is
-- a per-release reading. If a future UNII release stops populating a substance's
-- INN_ID, the moiety must stay while that evidence row must be able to go;
-- otherwise the table would keep asserting something the current release does not
-- say. The UNII orchestrator deletes and re-inserts inside the run's transaction,
-- exactly like class_membership and local_product.
--
-- NOTE the direction of the asymmetry, because it is the design and it is easy to
-- "simplify" away: a STRONG signal (INN_ID, USAN_ID) admits OUTRIGHT, whatever
-- the substance type. 571 records carry an INN_ID with a non-drug-like
-- SUBSTANCE_TYPE, and they include heparin sodium, enoxaparin sodium, protamine
-- sulfate, iron sucrose and 346 gene/cell therapies. Applying the type filter
-- uniformly would delete heparin from a drug-interaction service -- a far worse
-- error than admitting a botanical. SUBSTANCE_TYPE is a chemistry classification;
-- it was never meant to answer "is this a medicine", so it may only ever qualify
-- the WEAK signal (an RxCUI, which RxNorm also assigns to excipients and
-- homeopathic preparations).

CREATE TABLE IF NOT EXISTS drugref.moiety_admission (
    moiety_uuid uuid   NOT NULL REFERENCES drugref.substance_moiety(moiety_uuid),
    signal      text   NOT NULL,
    ingest_run  bigint NOT NULL REFERENCES drugref.ingest_run(ingest_run_id),
    PRIMARY KEY (moiety_uuid, signal),

    -- A CLOSED vocabulary, named after the UNII column each signal is read from
    -- (LEGACY_ALLOWLIST is drugref's own curated source). Closed on purpose: an
    -- unconstrained column would let a typo become a silent fifth category that
    -- every GROUP BY over this table then under-reports, with no error anywhere.
    -- Widening it means widening gate.admission_signals in the same change --
    -- that coupling is deliberate, and it is one edit, not a hidden one.
    CONSTRAINT moiety_admission_signal
        CHECK (signal IN ('INN_ID', 'USAN_ID', 'RXCUI', 'LEGACY_ALLOWLIST'))
);

-- The #19 worklist query -- "which moieties rest on the weakest evidence?" --
-- groups by moiety, and the conservation check ("is any moiety unexplained?")
-- is an anti-join on the same key, both of which the PK's leading column serves.
-- The other direction, "how many moieties does each signal carry?", does not:
COMMENT ON TABLE drugref.moiety_admission IS
    'Rebuildable projection: why each moiety passed the membership gate (#26). '
    'Set-valued -- a moiety admitted on several signals has a row per signal. '
    'Rebuilt by the UNII orchestrator every run; NOT append-only, because the '
    'moiety is immortal but the evidence is a per-release observation.';

CREATE INDEX IF NOT EXISTS moiety_admission_by_signal
    ON drugref.moiety_admission (signal);
