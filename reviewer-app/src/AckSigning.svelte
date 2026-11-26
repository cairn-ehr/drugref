<script lang="ts">
  /**
   * Signing departure acknowledgements (db/088, #284). An acknowledgement silences a
   * CHANGED SINCE REVIEW notice, so it is signed like a ruling. One acknowledgement
   * can close many pairs, each recorded (and signed) separately, so this signs a
   * batch: every payload is shown, the vault is unlocked once, and the service
   * verifies and records all of them or none.
   */

  import { onMount } from "svelte";
  import {
    ACK_SIGNATURE_BATCH_MAX,
    SIGNING_PASSPHRASE_MAX_LENGTH,
    SIGNING_PASSPHRASE_MIN_LENGTH,
  } from "./lib/constants";
  import { completeAckSignatures, loadUnsignedDepartureAcks, prepareAckSignatures } from "./lib/ackSigning";
  import { workingRecordDate } from "./lib/presentation";
  import { loadSigningStatus } from "./lib/signing";
  import type { AckSignaturePreview, AckSignatureQuery, DeviceSigningStatus, UnsignedDepartureAck } from "./lib/types";

  /** Human labels for the frozen field names; unknown future names stay visible. */
  const FIELD_LABELS: Readonly<Record<string, string>> = {
    rule_grain: "Ruling grain",
    subject_key: "Ruling subject",
    object_class_uuid: "Object class",
    relationship: "Relationship",
    subject_moiety: "Subject drug",
    partner_moiety: "Partner drug",
    episode_ordinal: "Episode of this pair",
    rationale: "Rationale",
    reviewed_by: "Acknowledged by",
    reviewed_against: "Reviewed against",
    acknowledged_at: "Acknowledged at",
    signer_key_fingerprint: "Signing key fingerprint",
    signed_at: "Signing instant",
  };

  /** Reload trigger: the parent bumps it after each acknowledgement. */
  let { version = 0 }: { version?: number } = $props();

  let acks = $state<UnsignedDepartureAck[]>([]);
  let status = $state<DeviceSigningStatus | null>(null);
  let preview = $state<AckSignaturePreview | null>(null);
  let query = $state<AckSignatureQuery | null>(null);
  let passphrase = $state("");
  let loaded = $state(false);
  let busy = $state(false);
  let errorMessage = $state("");
  let successMessage = $state("");

  let activeKey = $derived(
    status?.keys.find(
      (key): boolean => key.status === "active" && key.keyFingerprint === status?.localKeyFingerprint,
    ) ?? null,
  );
  let batch = $derived(acks.slice(0, ACK_SIGNATURE_BATCH_MAX));

  onMount((): void => void refresh());
  $effect((): void => {
    if (version > 0) void refresh();
  });

  /** Reload unsigned acknowledgements and this device's key; drops any open preview. */
  async function refresh(): Promise<void> {
    loaded = false;
    errorMessage = "";
    preview = null;
    query = null;
    passphrase = "";
    try {
      [acks, status] = await Promise.all([loadUnsignedDepartureAcks(), loadSigningStatus()]);
      loaded = true;
    } catch (error) {
      errorMessage = String(error);
    }
  }

  /** Ask the service for the batch's exact payloads; the native core retains them. */
  async function prepare(): Promise<void> {
    if (!activeKey || batch.length === 0) return;
    busy = true;
    errorMessage = "";
    successMessage = "";
    try {
      const next: AckSignatureQuery = {
        ackIds: batch.map((ack) => ack.ackId),
        keyFingerprint: activeKey.keyFingerprint,
      };
      preview = await prepareAckSignatures(next);
      query = next;
    } catch (error) {
      errorMessage = String(error);
    } finally {
      busy = false;
    }
  }

  /** Unlock locally, sign every confirmed payload, and reload what still needs signing. */
  async function complete(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!preview || !query) return;
    busy = true;
    errorMessage = "";
    try {
      const done = await completeAckSignatures(query, preview, passphrase);
      await refresh();
      successMessage = `${done.recorded} ${done.recorded === 1 ? "signature" : "signatures"} verified and recorded.`;
    } catch (error) {
      // The retained challenge cannot succeed again (expired, changed, or already
      // recorded by an attempt whose reply was lost): drop it and reload, so the list
      // is current and Prepare is offered, keeping the error on screen.
      const message = String(error);
      await refresh();
      errorMessage = message;
    } finally {
      busy = false;
    }
  }

  /** Compact a digest while keeping both ends for visual comparison. */
  function compact(value: string): string {
    return `${value.slice(0, 18)}…${value.slice(-12)}`;
  }
</script>

<section class="review-signing ack-signing" aria-live="polite">
  <div class="review-signing-head">
    <div><span class="key-indicator"><span></span> Unsigned acknowledgements · {loaded ? acks.length : "–"}</span><small>Each acknowledgement silences a CHANGED SINCE REVIEW notice; signing makes it tamper-evident.</small></div>
    {#if loaded && acks.length > 0 && !preview}
      <button class="primary-button primary-button--compact" type="button" disabled={busy || !activeKey || !status?.localVaultExists} onclick={() => void prepare()}>{busy ? "Preparing…" : `Prepare ${batch.length} ${batch.length === 1 ? "signature" : "signatures"}`}</button>
    {/if}
  </div>
  {#if loaded && acks.length > 0 && !activeKey}<p class="record-status">Enrol this device's key under Signing keys before signing acknowledgements.</p>{/if}
  {#if errorMessage}<p class="form-error" role="alert">{errorMessage}</p>{/if}
  {#if successMessage}<p class="form-success" role="status">{successMessage}</p>{/if}
  {#if loaded && !preview}
    {#each acks as ack (ack.ackId)}
      <p class="pending-attribution">#{ack.ackId} · {ack.subjectMoietyName} × {ack.partnerMoietyName} ({ack.subjectName} → {ack.objectClassName}) · {ack.reviewedBy}, {workingRecordDate(ack.acknowledgedAt)}{ack.signatureStatus === "unsigned" ? "" : ` · ${ack.signatureStatus.replaceAll("_", " ")}`}</p>
    {:else}
      <p class="admin-empty">No acknowledgement awaits a signature (registry status; <code>drugref verify</code> checks the signatures themselves).</p>
    {/each}
    {#if acks.length > batch.length}<p class="record-status">Signing the first {batch.length}; prepare again for the rest.</p>{/if}
  {/if}
  {#if preview && query}
    <form class="signature-confirmation" onsubmit={complete}>
      <p class="section-label">Confirm exact attestations</p>
      <strong>{preview.items.length} {preview.items.length === 1 ? "acknowledgement" : "acknowledgements"}</strong>
      {#each preview.items as item (item.ackId)}
        <details class="signed-content">
          <summary>Acknowledgement #{item.ackId} · {compact(item.payloadDigest)}</summary>
          <dl class="signed-field-list">
            {#each item.fields as field, index (`${index}-${field.name}`)}
              <div class:signed-field--wide={field.name === "rationale"}>
                <dt><span>{FIELD_LABELS[field.name] ?? field.name.replaceAll("_", " ")}</span><code>{field.name}</code></dt>
                <dd class:signed-field-null={field.value === null}>{field.value ?? "NULL — no value recorded"}</dd>
              </div>
            {/each}
          </dl>
        </details>
      {/each}
      <dl class="signature-metadata"><div><dt>Context</dt><dd>{preview.payloadContext}</dd></div><div><dt>Key</dt><dd title={preview.keyFingerprint}>{compact(preview.keyFingerprint)}</dd></div><div><dt>Signing instant</dt><dd>{preview.signedAt}</dd></div></dl>
      <label><span>Signing-vault passphrase</span><input type="password" autocomplete="off" name="signing-vault-passphrase" required minlength={SIGNING_PASSPHRASE_MIN_LENGTH} maxlength={SIGNING_PASSPHRASE_MAX_LENGTH} bind:value={passphrase} /></label>
      <p>Open each acknowledgement to review its exact signed values. The service rebuilds every payload and verifies every signature before recording any of them.</p>
      <div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={busy} onclick={() => void refresh()}>Cancel</button><button class="primary-button primary-button--compact" type="submit" disabled={busy}>{busy ? "Signing locally…" : "Sign and verify all"}</button></div>
    </form>
  {/if}
</section>
