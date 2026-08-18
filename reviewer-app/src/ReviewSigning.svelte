<script lang="ts">
  /** Explicit prepare-confirm-sign flow for one current immutable revision. */

  import { onMount } from "svelte";
  import {
    SIGNING_PASSPHRASE_MAX_LENGTH,
    SIGNING_PASSPHRASE_MIN_LENGTH,
  } from "./lib/constants";
  import {
    completeReviewSignature,
    loadSigningStatus,
    prepareReviewSignature,
  } from "./lib/signing";
  import type {
    CanonicalField,
    DeviceSigningStatus,
    ReviewDecisionRecord,
    ReviewRecordQuery,
    ReviewSignaturePreview,
    ReviewSignatureQuery,
  } from "./lib/types";

  /** Human labels for the frozen canonical field names. Unknown future names remain visible. */
  const SIGNED_FIELD_LABELS: Readonly<Record<string, string>> = {
    subject_moiety_uuid: "Subject moiety",
    object_class_uuid: "Object class",
    object_condition_uuid: "Object condition",
    relationship: "Relationship",
    applies: "Applies",
    ruling: "Ruling",
    severity: "Severity",
    mechanism: "Mechanism",
    management: "Management",
    evidence_grade: "Evidence grade",
    question_uuid: "Question",
    source: "Source",
    reviewed_by: "Reviewed by",
    reviewed_against: "Reviewed against",
    reviewed_at: "Reviewed at",
    signer_key_fingerprint: "Signing key fingerprint",
    signed_at: "Signing instant",
  };

  /** Long clinical and provenance values receive a complete row in the field grid. */
  const FULL_WIDTH_SIGNED_FIELDS = new Set(["mechanism", "management", "reviewed_against"]);

  /** Visible marker for a canonical SQL NULL rather than an empty or omitted field. */
  const NULL_SIGNED_VALUE = "NULL — no value recorded";

  let {
    item,
    record,
    onSigned,
  }: {
    item: ReviewRecordQuery;
    record: ReviewDecisionRecord;
    onSigned: (record: ReviewDecisionRecord) => void;
  } = $props();
  let status = $state<DeviceSigningStatus | null>(null);
  let preview = $state<ReviewSignaturePreview | null>(null);
  let loading = $state(true);
  let preparing = $state(false);
  let signing = $state(false);
  let errorMessage = $state("");
  let successMessage = $state("");
  let passphrase = $state("");

  let current = $derived(
    record.history.find((revision): boolean => revision.revisionId === record.currentRevisionId) ??
      null,
  );
  let activeKey = $derived(
    status?.keys.find(
      (key): boolean =>
        key.status === "active" && key.keyFingerprint === status?.localKeyFingerprint,
    ) ?? null,
  );
  let canPrepare = $derived(
    Boolean(current && current.signatureStatus === "unsigned" && activeKey && status?.localVaultExists),
  );

  onMount((): void => void refreshStatus());

  /** Load current key enrolment and local-vault state. */
  async function refreshStatus(): Promise<void> {
    loading = true;
    errorMessage = "";
    try {
      status = await loadSigningStatus();
    } catch (error) {
      errorMessage = String(error);
    } finally {
      loading = false;
    }
  }

  /** Ask the service for exact row content and retain native canonical bytes. */
  async function prepare(): Promise<void> {
    if (!current || !activeKey) return;
    preparing = true;
    errorMessage = "";
    successMessage = "";
    try {
      preview = await prepareReviewSignature(query(current.revisionId, activeKey.keyFingerprint));
    } catch (error) {
      errorMessage = String(error);
    } finally {
      preparing = false;
    }
  }

  /** Confirm the displayed digest, unlock locally, and submit for service verification. */
  async function complete(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!preview) return;
    signing = true;
    errorMessage = "";
    try {
      const updated = await completeReviewSignature(
        query(preview.revisionId, preview.keyFingerprint),
        preview,
        passphrase,
      );
      passphrase = "";
      preview = null;
      successMessage = "Signature verified, recorded, and reloaded from the database.";
      onSigned(updated);
    } catch (error) {
      errorMessage = String(error);
    } finally {
      signing = false;
    }
  }

  /** Build the exact target, revision, and key selector used by both protocol steps. */
  function query(revisionId: number, keyFingerprint: string): ReviewSignatureQuery {
    return {
      kind: item.kind,
      targetKey: item.targetKey,
      revisionId,
      keyFingerprint,
    };
  }

  /** Compact a full digest while keeping both ends available for visual comparison. */
  function compact(value: string): string {
    return `${value.slice(0, 18)}…${value.slice(-12)}`;
  }

  /** Convert a frozen machine field to a readable heading without hiding its exact name. */
  function signedFieldLabel(name: string): string {
    return SIGNED_FIELD_LABELS[name] ?? name.replaceAll("_", " ");
  }

  /** Mark narrative values that need the complete available width. */
  function fullWidthSignedField(field: CanonicalField): boolean {
    return FULL_WIDTH_SIGNED_FIELDS.has(field.name);
  }
</script>

<section class="review-signing" aria-live="polite">
  <div class="review-signing-head">
    <div><span class="key-indicator"><span></span> Detached device signature</span><small>Authentication records the row; this separate action attests it.</small></div>
    {#if current?.signatureStatus === "unsigned"}
      <button class="primary-button primary-button--compact" type="button" disabled={loading || preparing || !canPrepare} onclick={() => void prepare()}>{preparing ? "Preparing…" : "Prepare signature"}</button>
    {:else if current}
      <span class="signature-verdict">{current.signatureStatus}</span>
    {/if}
  </div>
  {#if !loading && !activeKey}<p class="record-status">Enrol this device's key under Signing keys before signing this revision.</p>{/if}
  {#if errorMessage}<p class="form-error" role="alert">{errorMessage}</p>{/if}
  {#if successMessage}<p class="form-success" role="status">{successMessage}</p>{/if}
  {#if preview && current}
    <form class="signature-confirmation" onsubmit={complete}>
      <p class="section-label">Confirm exact attestation</p>
      <strong>Revision #{preview.revisionId} · {current.decision.replaceAll("_", " ")}</strong>
      <section class="signed-content" aria-labelledby="signed-content-title">
        <div class="signed-content-head"><div><p class="section-label">Complete signed content</p><h3 id="signed-content-title">Review every value below</h3></div><span>{preview.fieldCount} fields</span></div>
        <p>These are the exact named values covered by the digest, in canonical order. Long clinical text is shown in full.</p>
        <dl class="signed-field-list">
          {#each preview.fields as field, index (`${index}-${field.name}`)}
            <div class:signed-field--wide={fullWidthSignedField(field)}>
              <dt><span>{signedFieldLabel(field.name)}</span><code>{field.name}</code></dt>
              <dd class:signed-field-null={field.value === null}>{field.value ?? NULL_SIGNED_VALUE}</dd>
            </div>
          {/each}
        </dl>
      </section>
      <dl class="signature-metadata"><div><dt>Context</dt><dd>{preview.payloadContext}</dd></div><div><dt>Payload SHA-256</dt><dd title={preview.payloadDigest}>{compact(preview.payloadDigest)}</dd></div><div><dt>Key</dt><dd title={preview.keyFingerprint}>{compact(preview.keyFingerprint)}</dd></div><div><dt>Signing instant</dt><dd>{preview.signedAt}</dd></div></dl>
      <label><span>Signing-vault passphrase</span><input type="password" autocomplete="off" name="signing-vault-passphrase" required minlength={SIGNING_PASSPHRASE_MIN_LENGTH} maxlength={SIGNING_PASSPHRASE_MAX_LENGTH} bind:value={passphrase} /></label>
      <p>The service will independently rebuild these bytes and verify the Ed25519 signature before inserting it. If the row changes first, signing is refused and can be prepared again.</p>
      <div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={signing} onclick={() => { preview = null; passphrase = ""; }}>Cancel</button><button class="primary-button primary-button--compact" type="submit" disabled={signing}>{signing ? "Signing locally…" : "Sign and verify"}</button></div>
    </form>
  {/if}
</section>
