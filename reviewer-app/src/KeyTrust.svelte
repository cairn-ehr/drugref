<script lang="ts">
  /** Administrator-only public signing-key trust and review-impact workflow. */

  import { onMount } from "svelte";
  import { administerSigningKey, loadSigningKeyTrust } from "./lib/signing";
  import { workingRecordDate } from "./lib/presentation";
  import type {
    AdministrativeSigningKeyStatus,
    SigningKeyTrustSummary,
  } from "./lib/types";

  let keys = $state<SigningKeyTrustSummary[]>([]);
  let selectedFingerprint = $state("");
  let loading = $state(true);
  let saving = $state(false);
  let confirmation = $state<AdministrativeSigningKeyStatus | null>(null);
  let errorMessage = $state("");
  let successMessage = $state("");

  let selected = $derived(
    keys.find((key): boolean => key.keyFingerprint === selectedFingerprint) ?? keys[0] ?? null,
  );

  onMount((): void => void refresh());

  /** Reload current registry state and retain one stable fingerprint selection. */
  async function refresh(): Promise<void> {
    loading = true;
    errorMessage = "";
    try {
      const status = await loadSigningKeyTrust();
      keys = status.keys;
      if (!keys.some((key): boolean => key.keyFingerprint === selectedFingerprint)) {
        selectedFingerprint = keys[0]?.keyFingerprint ?? "";
      }
    } catch (error) {
      errorMessage = String(error);
    } finally {
      loading = false;
    }
  }

  /** Select one public key and clear action-specific messages. */
  function selectKey(key: SigningKeyTrustSummary): void {
    selectedFingerprint = key.keyFingerprint;
    confirmation = null;
    errorMessage = "";
    successMessage = "";
  }

  /** Append the confirmed status correction and adopt its fresh projection. */
  async function applyStatus(status: AdministrativeSigningKeyStatus): Promise<void> {
    if (!selected) return;
    saving = true;
    errorMessage = "";
    successMessage = "";
    try {
      const result = await administerSigningKey(selected.keyFingerprint, status);
      keys = keys.map((key): SigningKeyTrustSummary =>
        key.keyFingerprint === result.key.keyFingerprint ? result.key : key,
      );
      confirmation = null;
      successMessage = status === "compromised"
        ? `Compromise recorded. ${revisionCountLabel(result.revisionsAwaitingCounterSignature)} now require an unobjected counter-signature.`
        : `Retirement recorded. Earlier signatures remain governed by the time-scoped boundary.${result.withdrawnEnrolment ? " The reviewer enrolment was withdrawn." : ""}`;
    } catch (error) {
      errorMessage = String(error);
    } finally {
      saving = false;
    }
  }

  /** Render enough fingerprint context for lists while details retain the full value. */
  function shortFingerprint(fingerprint: string): string {
    return `${fingerprint.slice(0, 12)}…${fingerprint.slice(-8)}`;
  }

  /** Describe current affected revisions without implying clinical rows are withdrawn. */
  function revisionCountLabel(count: number): string {
    return `${count} current ${count === 1 ? "revision" : "revisions"}`;
  }

  /** Apply the currently visible confirmation without a nullable assertion. */
  function applyConfirmedStatus(): void {
    if (confirmation) void applyStatus(confirmation);
  }
</script>

<section class="trust-layout">
  <article class="admin-card trust-list-card">
    <div class="admin-card-head">
      <div><p class="section-label">Public registry</p><h2>Key trust</h2></div>
      <div class="admin-card-actions"><span class="count-pill">{keys.length}</span><button class="secondary-button" type="button" disabled={loading} onclick={() => void refresh()}>Refresh</button></div>
    </div>
    {#if loading}<p class="admin-empty">Loading signing-key trust…</p>{/if}
    {#if errorMessage && keys.length === 0}<div class="record-error" role="alert"><span>{errorMessage}</span><button type="button" onclick={() => void refresh()}>Try again</button></div>{/if}
    <div class="trust-list">
      {#each keys as key (key.keyFingerprint)}
        <button class="trust-row" class:trust-row--selected={selected?.keyFingerprint === key.keyFingerprint} type="button" onclick={() => selectKey(key)}>
          <span><strong>{key.reviewerFullName ?? key.holder}</strong><small>{key.username ? `@${key.username}` : "Registry-only key"} · {shortFingerprint(key.keyFingerprint)}</small></span>
          <span class="trust-row-state" class:trust-row-state--alert={key.status === "compromised"}><b>{key.status}</b><small>{key.affectedCurrentRevisionCount ? `${key.affectedCurrentRevisionCount} need counter-signing` : `${key.signatureCount} signatures`}</small></span>
        </button>
      {:else}
        {#if !loading}<p class="admin-empty">No public signing keys are registered.</p>{/if}
      {/each}
    </div>
  </article>

  <article class="admin-card trust-detail-card">
    {#if selected}
      <div class="admin-card-head">
        <div><p class="section-label">Append-only trust administration</p><h2>{selected.reviewerFullName ?? selected.holder}</h2><small class="immutable-username">{selected.algorithm} · {selected.enrolled ? "enrolled" : "not enrolled"}</small></div>
        <span class="account-state" class:account-state--disabled={selected.status !== "active"}>{selected.status}</span>
      </div>
      <div class="trust-detail-scroll">
        <dl class="trust-facts">
          <div class="trust-fingerprint"><dt>Full fingerprint</dt><dd>{selected.keyFingerprint}</dd></div>
          <div><dt>Status from</dt><dd>{workingRecordDate(selected.statusFrom)}</dd></div>
          <div><dt>Registry correction</dt><dd>{workingRecordDate(selected.registeredAt)}</dd></div>
          <div><dt>All signatures</dt><dd>{selected.signatureCount}</dd></div>
          <div><dt>Current revisions signed</dt><dd>{selected.currentRevisionCount}</dd></div>
          <div><dt>Awaiting counter-signature</dt><dd>{selected.affectedCurrentRevisionCount}</dd></div>
        </dl>

        <div class="trust-policy-note">
          <p class="section-label">Counter-signing policy</p>
          <p>Clinical rows remain served. A revision enters Pending signatures only when no registry-unobjected signature remains; one independent unobjected signature removes it from that queue.</p>
        </div>

        <div class="trust-actions">
          <section>
            <p class="section-label">Time-scoped</p><h3>Retire future use</h3>
            <p>Preserve signatures made before the retirement boundary and withdraw any current reviewer enrolment.</p>
            <button class="secondary-button" type="button" disabled={saving || selected.status !== "active"} onclick={() => confirmation = "retired"}>Retire key…</button>
          </section>
          <section class="trust-danger-zone">
            <p class="section-label">Blanket objection</p><h3>Record compromise</h3>
            <p>Object to every historical signature from this fingerprint and surface affected current revisions for counter-signing.</p>
            <button class="danger-button" type="button" disabled={saving || selected.status === "compromised"} onclick={() => confirmation = "compromised"}>Mark compromised…</button>
          </section>
        </div>

        {#if confirmation}
          <div class="account-confirmation account-confirmation--danger trust-confirmation" role="alert">
            {#if confirmation === "compromised"}
              <p><strong>Record a permanent compromise?</strong> All {selected.signatureCount} historical signatures from this key will be objected to. Up to {revisionCountLabel(selected.currentRevisionCount)} may need an independent counter-signature; the service will return the exact current count.</p>
            {:else}
              <p><strong>Retire this key?</strong> Future signing will stop and any live enrolment will be withdrawn. Signatures made before the new boundary remain unobjected.</p>
            {/if}
            <div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={saving} onclick={() => confirmation = null}>Cancel</button><button class="danger-button" type="button" disabled={saving} onclick={applyConfirmedStatus}>{saving ? "Recording…" : confirmation === "compromised" ? "Record compromise" : "Record retirement"}</button></div>
          </div>
        {/if}
        {#if errorMessage}<p class="form-error account-message" role="alert">{errorMessage}</p>{/if}
        {#if successMessage}<p class="form-success account-message" role="status">{successMessage}</p>{/if}
      </div>
    {:else}
      <p class="admin-empty">Choose a public key to inspect its trust state.</p>
    {/if}
  </article>
</section>
