# Design — drugref.org v2, global tier, slice 1: the active-moiety identity spine

**Date:** 2026-07-23 · **Repo (new):** `github.com/cairn-ehr/drugref` · **Status:** design, pending
implementation plan.

**Scope of change:** stand up a **new, separate repo** `cairn-ehr/drugref` (AGPL-3.0) — a co-equal
public-good drug-information service — and deliver its first slice: a **registry of active drug moieties,
each with an immortal UUID and append-only external-identifier claims**, seeded reproducibly from
public-domain sources (UNII/GSRS + ChEBI + INN, RxNorm demoted to a claim). Python ingest + a Postgres
schema `drugref` designed to co-reside in a Cairn deployment's Postgres or run standalone. **No HTTP API,
no salts/formulations, no drug classes, no interactions, no local/PBS tier, no Cairn `inn_code` wiring in
this slice.** Those are named later slices.

This graduates the sourcing/licensing research in
[ecosystem eval 0003](../../ecosystem/0003-reference-data-sourcing-medicines-and-terminologies.md) and the
identity-anchor pattern of [ADR-0025](../../spec/decisions/0025-icd-11-canonical-interlingua-and-local-terminology-overlay.md)
(stable identifier underneath, plural naming on top) into a real reference-data service. It also fills the
deliberately-nullable `inn_code` slot left open by the
[medication-recording design](2026-07-11-medication-recording-design.md) — but the *consumer wiring* is a
future slice; slice 1 is drugref-internal.

---

## 1. What drugref v2 is, and how it relates to Cairn

drugref v2 is a **drug-information service provider**, revived under the drugref.org umbrella, designed
from day one as a **co-equal public good**: any EHR / pharmacy system / app can consume it, and Cairn is
its first and best-integrated client **on exactly the same public-API footing as any third party** — the
[ADR-0021](../../spec/decisions/0021-layering-the-node-api-and-ui-pluralism.md) "even the steward's
reference UI uses only the public API" posture, applied to reference data. This follows the general
principle of making work sharable so effort is not duplicated across projects that need the same service.

**Two axes must be kept distinct** (the reconciliation of "separable tier" vs. "lives within Cairn"):

- **Co-location — yes.** The `drugref` schema is designed to run *inside* a Cairn deployment's PostgreSQL
  (fat-Postgres, [ADR-0001](../../spec/decisions/0001-fat-postgres-thin-daemon.md)) so a co-located Cairn
  node reaches it as plain SQL/views, *or* to run standalone for non-Cairn consumers.
- **On the signed inter-node wire core — never.** drugref is **advisory reference data**, not signed
  clinical events; nothing in it sits on Cairn's inter-node sync path or safety floor. This is the
  eval-0003 "separable external tier … a licence-encumbered source simply doesn't attach" guarantee, and
  it is what keeps a future NonCommercial data layer from ever contaminating Cairn's interoperability.
  Founding principle 12 (*uniform core, plural edges*): drugref is a plural edge.

**Two tiers, global first** (the user's split): a **global tier** (jurisdiction-independent — INN,
substance identity, chemistry, pharmacology, classes, interactions) and a **local tier** (country-specific
packaging/pricing — e.g. Australian PBS/TGA). This spec is **global tier, slice 1**.

## 2. The store: a deliberate hybrid (why, and what slice 1 touches)

drugref's data has two natures, and conflating them is the trap:

- **Ingested feed data** (UNII, ChEBI, INN, RxNorm) is a **rebuildable projection of an upstream
  authority.** WHO/FDA/NLM/EMBL-EBI *are* the source of truth; if drugref lost this it would re-ingest.
  It needs **versioning, provenance, and reproducible rebuild** — not immortality or signatures.
- **Curated value-add** (interaction severity/mechanism/management, clinical caveats, corrections to feed
  errors) is **original, scarce, medico-legally weighty human knowledge** — "the curation is the moat, the
  part no one gives away" (eval 0003 §4/§5). It wants the Cairn discipline: **append-only, never-erase-
  always-overlay, signed authorship, auditable history** — an *institutionally-owned append-only overlay*
  (principle 1), never a volunteer wiki (the failure mode that soft-killed drugref v1).

So the store mirrors a Cairn node's own split — **immutable log + rebuildable projection** — and that
makes drugref legible to Cairn people and lets the curated overlay itself sync set-union between drugref
instances later.

**Slice 1 lives entirely in the ingested/rebuildable half**, with one borrowed piece of the curated
discipline: the **substance UUID and its identity claims are append-only** (never re-key, never erase),
because substance *identity* is the immortal spine both halves hang off. Interactions/pharmacology (the
fully-curated overlay) are later slices.

## 3. The data model in the large (two orthogonal structures) — and what slice 1 builds

The global tier is **two orthogonal structures**, not one tree:

1. **Composition tree** (downward *is-made-of*): **active moiety → specific substance (salt/ester/
   hydrate) → clinical drug (moiety/salt + strength + form) → product (brand/pack)**. Product is the
   *local* tier.
2. **Classification graph** (an orthogonal DAG, *is-a-kind-of*): `class ⊂ class ⊂ …`, and
   `moiety ∈ many classes` on multiple axes (chemical / mechanism / therapeutic). Membership is
   **many-to-many** — a link, never a parent foreign key. (Modelling class as a moiety attribute is what
   forces the "must be NULLable / blocks seeding" symptom; it is a *relationship*, not a column.)

The curated overlay attaches to nodes in **either** structure and **inherits along the edges** — *down*
the composition tree (curate at the moiety, inherits to its salts/formulations) and *up* from a moiety
through **every class it belongs to** (curate "ACE-inhibitor + K-sparing → hyperkalaemia" once at class
level, inherits to all members). This inheritance is the single biggest **curation-economy** lever in the
system, and it is why classes are load-bearing to the interaction mission — not mere taxonomy.

> [!NOTE]
> **Slice 1 builds only the top node of the composition tree — the active moiety — plus its identity
> claims.** The class DAG, membership, salts, formulations, and the curated overlay are **out of scope**
> (§9), but the schema is shaped to admit them without rework (§4).

## 4. Slice-1 schema (`drugref` Postgres schema — three tables)

- **`substance_moiety`** — the registry. `moiety_uuid UUID PRIMARY KEY` (minted once, immortal), a display
  name (the INN-preferred label, §6), and `first_seen_ingest` (FK → `ingest_run`). Deliberately thin;
  everything identifying lives in claims.
- **`identity_claim`** — **append-only**, many-per-moiety:
  `(moiety_uuid, scheme, value, ingest_run, asserted_at, superseded_by)`, with
  `scheme ∈ {UNII, INN, RXNORM_IN, CHEBI, CAS, PUBCHEM_CID, …}`. This is the principle-2 move applied to
  substances: **external identifiers are claims that attach, never the key.** A correction **overlays** (a
  new row, `superseded_by` set on the old) — never `UPDATE`/`DELETE`.
  **Capture the full cross-reference set at seed.** We are already parsing UNII, INN, RxCUI, and the
  ChEBI/CAS/PubChem cross-refs to build the registry, so recording *all* of them as claims is near-free —
  and it makes drugref a useful **public identifier cross-walk** for other projects (UNII↔INN↔RxCUI↔ChEBI
  in one place), which serves the co-equal-public-good goal (§1) at essentially no extra cost.
- **`ingest_run`** — provenance: `(source, upstream_release, source_checksum, started_at, finished_at)`.
  Every registry and claim row traces to one `ingest_run`, so any state is reproducible and attributable
  to a specific upstream release.

**Forward-compatible by construction** (admits later slices without rework): the composition tree adds a
`specific_substance` table with `parent_moiety_uuid`; classes add `substance_class` + a
`class_membership(moiety_uuid, class_uuid, …)` many-to-many link table + `class_parent` for the DAG; the
curated overlay adds its own append-only, signed event tables.

## 5. Substance UUID minting — the immortality mechanic

- **drugref mints its own immortal `moiety_uuid`** (principle 2: identity is a claim, never the name;
  never merge, always link). INN is the *primary human anchor*, **not** the key — INN is a name with
  national divergence (paracetamol/acetaminophen), salt-granularity ambiguity, and pre-/never-INN gaps,
  so keying the join on it repeats "key on the patient's name," the wound Cairn was built around.
- **Deterministic seed, immortal thereafter.** Mint as **`UUIDv5(moiety_namespace, "UNII:" + unii)`** of
  the *active moiety* at first sighting — so two independent drugref instances ingesting the same UNII
  release derive the **same** UUID with zero coordination (content-addressed, no central registry — the
  [ADR-0014](../../spec/decisions/0014-locale-pluggable-matcher-comparators.md) posture). **Pin on first
  sight**: thereafter the registry/`ingest_run` is authoritative, and upstream churn (a UNII/RxCUI remap
  or retire) attaches a **new claim or link**, and **never re-derives** the UUID.
- **Per-level namespace constants** (moiety / specific-substance / class each their own UUIDv5 namespace)
  so derivations can never collide across levels.
- **Cross-source identity is a link/matcher problem, not a re-key** (later slices): if a second source
  presents a moiety already registered (recognised via a crosswalk claim), it adds a claim; if genuine
  ambiguity, it links — never destructively remaps. Slice 1 is single-backbone (UNII), so this does not
  yet arise.

## 6. Seeding — international by construction (the sourcing decision)

RxNorm's *structure* is useful but its *names* are US-centric (acetaminophen, albuterol, meperidine), so
it is **demoted from naming backbone to an attached claim**. The seed backbone is international:

| Role | Source | Licence |
|---|---|---|
| **Identity backbone / UUID key** | **UNII (FDA GSRS)** — ISO 11238, structure-based, international; covers chemical + protein + nucleic-acid + polymer + mixture (biologicals first-class); exposes **active-moiety relationships** (salt→base, used by the later salt slice) | **public domain** ✓ |
| **INN display anchor** (preferred over the US name) | INN-typed name from **ChEBI** + GSRS name-type | ChEBI **CC BY 4.0** / PD ✓ |
| **Chemistry + cross-IDs** (CAS, PubChem, InChI/SMILES) | **ChEBI** | **CC BY 4.0** ✓ |
| **US-interop + future SCD hierarchy** | **RxNorm** RxCUI — *attached claim, not the naming backbone* | **public domain** ✓ |
| **USAN↔INN legacy crosswalk** | **hand-curated, one-time** — drugref's own asset | our own, licence-clean ✓ |

**The USAN↔INN divergence is a closed, non-growing legacy set** — the decisive finding. The AMA USAN-First
policy files the INN application on the firm's behalf; *"in most cases USAN and INN are identical"*; new
drugs are born with **INN = USAN**. Divergences are only entrenched *historical* names (a few dozen
prominent ones; ≤ ~300 counting spelling conventions like the INN "f/ph"). So hand-curating the crosswalk
is a **one-time bounded job that accrues no maintenance treadmill** — it is drugref's own licence-clean
mapping asset (attribution to WHO/USP for the underlying names).

**Licences all AGPL-bundleable** for slice 1 (UNII PD, ChEBI CC BY 4.0, RxNorm prescribable PD, INN PD).
Per eval-0003 discipline, the exact ChEBI CC BY 4.0 deed and UNII/GSRS distribution terms go on the
**verify-before-bundle** list, and a `NOTICE`/attribution file ships in the repo.

### 6.1 The moiety-membership gate (decided: has-INN + legacy allow-list)

UNII enumerates **every** substance (excipients, foods, thousands of non-drugs), so *"which UNIIs are
active drug moieties?"* needs an explicit **membership gate**. **Decision: `has-INN` is the gate** — a
substance with a WHO INN *is* an active pharmaceutical moiety by definition, and it is the cleanest,
most-defensible, jurisdiction-neutral criterion. It is **supplemented by a small explicit allow-list for
pre-INN legacy drugs** (aspirin, caffeine, and similar substances that predate the INN programme or will
never receive an INN, including some biologicals) so the registry does not silently drop staple drugs —
that allow-list is drugref's own curated data, small and closed like the USAN↔INN crosswalk. RxNorm-IN
membership and ChEBI drug-role are **not** gate criteria but remain useful *cross-check signals* for
auditing the gate's yield (an active-moiety-looking substance with no INN and not on the allow-list is a
worklist item, never a silent exclude). The main engineering risk is INN's known access friction
(eval 0003: "no clean bulk file" — registration-gated Global Data Hub API, or parse the biannual
public-domain PDF lists); resolving that access path is a first plan step.

## 7. Substrate, structure, and the in-DB integrity floor

Per the language-substrate rule ([spec §9](../../spec/language-substrate.md)), drugref is the **advisory /
fit-for-purpose** tier — a defect mis-advises but does not corrupt a signed clinical record and is caught
by the clinician who decides — so ingest optimises for **iteration speed**:

- **Python ingest** (`drugref/ingest/…`) — feed download, parse, normalise, mint/pin UUIDs, write claims.
  Python is the user's language and the eval's advisory zone (same as the §5.2 matcher); the historical
  pain was brittle feed parsing, which Python handles well.
- **Postgres store + the integrity floor in the database, not app code.** Even though the *tier* is
  advisory, substance-identity *integrity* is not optional and is enforced **unbypassably in Postgres**
  (the principle-12 "floor in the DB" scaled to what this tier needs): `moiety_uuid` immutable once
  written; `identity_claim` **no-`UPDATE`/no-`DELETE`** (supersede-by-overlay only, via constraints/
  triggers/RLS). So a buggy ingest — or a raw-SQL hand — cannot silently rewrite substance identity.
- **Idempotent re-ingest.** Re-running a source yields identical UUIDs and `ON CONFLICT` overlays claims;
  no duplication. A rebuild from a fresh upstream release is a first-class operation.

## 8. Testing (TDD, failing-test-first)

- **Unit (Python, no DB):** UUIDv5 derivation is deterministic and stable for a fixed UNII; namespace
  separation prevents cross-level collision; claim overlay/supersede logic.
- **Integration (DB-gated), against a small fixture UNII/ChEBI subset:**
  - N active moieties minted; INN/UNII/ChEBI/RxCUI claims present and correctly typed.
  - **Idempotency:** re-run ⇒ identical `moiety_uuid`s, zero duplicate claims.
  - **Immortality:** a simulated upstream UNII/RxCUI remap ⇒ a **new claim**, `moiety_uuid` **unchanged**.
  - **Append-only floor:** an attempted `UPDATE`/`DELETE` on `identity_claim` or on `moiety_uuid` is
    **rejected by the DB**.
  - **Membership gate:** a non-drug substance (an excipient/food UNII) is **excluded** from the registry;
    a positive control (a known INN moiety) is **included**.
  - **USAN↔INN crosswalk:** acetaminophen's UNII surfaces **paracetamol** as the INN display anchor.
- Crypto material in tests is **runtime-derived, never literal** (house rule 6 / issue #146) — applies if
  any signing lands in a later curated-overlay slice; N/A to slice 1's identity spine.

## 9. Explicitly out of scope for slice 1 (each a later slice)

- **Composition tree below the moiety** — specific substances (salts/esters/hydrates) and clinical drugs
  (strength + form). GSRS active-moiety links are captured-for-later but not modelled yet.
- **Classification graph** — the class DAG + many-to-many membership (seeded from MED-RT + MeSH PA, which
  need their own licence verification — US-gov, *expected* public-domain but unvetted by eval 0003).
- **The curated overlay** — drug–drug interactions (the moat; ONC floor → SPL/ONSIDES-mined → curated
  append-only), pharmacology prose, class-level interaction rules. This is the append-only *signed* half
  of the hybrid store (§2).
- **ATC** — licence-blocked (WHO ATC/DDD is NC + no-derivatives); class backbone comes from MED-RT, and
  ATC attaches only as a node-local licensed plug-in, never bundled.
- **The HTTP public API** — the co-equal-consumer interface; co-located Cairn needs only the schema, so
  the API is deferred until there is data to serve.
- **The local tier** — PBS/TGA (Australia first) packaging/pricing, per eval 0003 §3.
- **Cairn `inn_code` wiring** — the medication surface's Tier-A overlay enrichment (autocomplete, coding a
  previously-uncoded substance). drugref stands up first; Cairn consumes it in a later, separate slice.

## 10. Design tensions recorded (resolved)

- **(A) INN as key vs. own UUID** → **own UUID**; INN is the top-ranked *attached claim* and the display
  anchor. Keying on the name repeats the founding wound.
- **(B) "separable tier" vs. "lives within Cairn"** → **co-located in Postgres, never on the wire core.**
  Two different axes; both satisfied.
- **(C) RxNorm US-centric names** → RxNorm **demoted to a claim**; UNII/ChEBI/INN are the international
  backbone. Identity spine is international by construction.
- **(D) UNII enumerates non-drugs** → an explicit **membership gate = `has-INN`**, plus a small closed
  allow-list for pre-INN legacy drugs (aspirin, caffeine) so staples are not silently dropped; RxNorm-IN /
  ChEBI-drug-role are audit cross-checks, not gate criteria.
- **(E) hand-translation a maintenance treadmill?** → **No.** USAN↔INN divergence is a *closed legacy
  set* (new drugs born harmonised); the crosswalk is a one-time bounded asset.
- **(F) advisory tier ⇒ lax integrity?** → **No.** Tier is advisory (Python ingest), but substance-
  identity integrity is enforced **unbypassably in the DB** (append-only floor).
- **(G) "unbypassable" floor overclaims** → the slice-1 DB floor enforces row-level UPDATE/DELETE
  immutability only; TRUNCATE and the table-owning role remain bypasses, closed in a later
  hardening slice via RLS + privilege separation (design §7's full floor). Accepted for slice 1
  because the identity spine is rebuildable reference data, not the signed clinical wire core.
