<script lang="ts">
  /** Device-local encrypted signing-key enrolment and public registry status. */

  import { onMount } from "svelte";
  import {
    SIGNING_PASSPHRASE_MAX_LENGTH,
    SIGNING_PASSPHRASE_MIN_LENGTH,
  } from "./lib/constants";
  import { workingRecordDate } from "./lib/presentation";
  import {
    enrolLocalSigningKey,
    loadSigningStatus,
    replaceLocalSigningKey,
  } from "./lib/signing";
  import type { DeviceSigningStatus } from "./lib/types";

  let { onEnrolled }: { onEnrolled: (count: number) => void } = $props();
  let status = $state<DeviceSigningStatus | null>(null);
  let loading = $state(true);
  let saving = $state(false);
  let replacing = $state(false);
  let confirmingReplacement = $state(false);
  let errorMessage = $state("");
  let successMessage = $state("");
  let passphrase = $state("");
  let confirmation = $state("");
  let localKey = $derived(
    status?.keys.find((key): boolean => key.keyFingerprint === status?.localKeyFingerprint) ?? null,
  );

  onMount((): void => void refresh());

  /** Refresh device and registry status through the native trust boundary. */
  async function refresh(): Promise<void> {
    loading = true;
    errorMessage = "";
    try {
      status = await loadSigningStatus();
      onEnrolled(status.keys.length);
    } catch (error) {
      errorMessage = String(error);
    } finally {
      loading = false;
    }
  }

  /** Generate or reopen a vault after explicit passphrase confirmation. */
  async function enrol(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    errorMessage = "";
    successMessage = "";
    if (passphrase !== confirmation) {
      errorMessage = "Signing passphrases do not match.";
      return;
    }
    saving = true;
    try {
      const key = await enrolLocalSigningKey(passphrase);
      passphrase = "";
      confirmation = "";
      successMessage = `Key ${shortFingerprint(key.keyFingerprint)} is enrolled and ready.`;
      await refresh();
    } catch (error) {
      errorMessage = String(error);
    } finally {
      saving = false;
    }
  }

  /** Retire the public enrolment before deleting the unrecoverable local key. */
  async function replaceKey(): Promise<void> {
    replacing = true;
    errorMessage = "";
    successMessage = "";
    try {
      const replacement = await replaceLocalSigningKey();
      passphrase = "";
      confirmation = "";
      confirmingReplacement = false;
      successMessage = replacement.registryStatus === "compromised"
        ? `${replacement.preservedSignatureCount} existing signatures remain in audit history under the compromise objection; this device's local vault was deleted.`
        : replacement.preservedSignatureCount === 0
          ? "The unused key was retired and its local vault deleted. You can create a replacement now."
          : `${replacement.preservedSignatureCount} existing signatures were preserved; the old key is ${replacement.registryStatus} and its local vault was deleted.`;
      await refresh();
    } catch (error) {
      errorMessage = String(error);
    } finally {
      replacing = false;
    }
  }

  /** Render enough of a fingerprint to distinguish device keys without visual noise. */
  function shortFingerprint(fingerprint: string): string {
    return `${fingerprint.slice(0, 12)}…${fingerprint.slice(-8)}`;
  }
</script>

<section class="signing-layout">
  <article class="admin-card signing-key-card">
    <div class="admin-card-head"><div><p class="section-label">Public registry</p><h2>Your signing keys</h2></div><span class="count-pill">{status?.keys.length ?? 0}</span></div>
    {#if loading}
      <p class="admin-empty">Loading signing-key status…</p>
    {:else if status?.keys.length}
      <div class="signing-key-list">
        {#each status.keys as key (key.keyFingerprint)}
          <article class="signing-key-row">
            <span class="key-indicator"><span></span>{key.algorithm} · {key.status}</span>
            <strong>{shortFingerprint(key.keyFingerprint)}</strong>
            <small>{key.holder} · enrolled {workingRecordDate(key.enrolledAt)} · {key.signatureCount} {key.signatureCount === 1 ? "signature" : "signatures"}</small>
          </article>
        {/each}
      </div>
    {:else}
      <p class="admin-empty">No public key is enrolled for this reviewer.</p>
    {/if}
  </article>

  <article class="admin-card signing-enrol-card">
    <div class="admin-card-head"><div><p class="section-label">This device</p><h2>{status?.localVaultExists ? "Resume local enrolment" : "Create a signing key"}</h2></div></div>
    <p class="admin-intro">{status?.localVaultExists ? "An encrypted Stronghold vault exists on this device. Unlock it to finish or repeat its idempotent public-key enrolment." : "Generate an Ed25519 key inside an encrypted Stronghold vault. Only its public half and fingerprint leave this device."}</p>
    <form class="admin-form" onsubmit={enrol}>
      <label><span>Signing-vault passphrase</span><input type="password" autocomplete="new-password" required minlength={SIGNING_PASSPHRASE_MIN_LENGTH} maxlength={SIGNING_PASSPHRASE_MAX_LENGTH} bind:value={passphrase} /></label>
      <label><span>Confirm signing passphrase</span><input type="password" autocomplete="new-password" required minlength={SIGNING_PASSPHRASE_MIN_LENGTH} maxlength={SIGNING_PASSPHRASE_MAX_LENGTH} bind:value={confirmation} /></label>
      <p class="vault-warning">This is separate from your account password. Drugref cannot recover it, and the private key is never sent to the review service or WebView.</p>
      {#if errorMessage}<p class="form-error" role="alert">{errorMessage}</p>{/if}
      {#if successMessage}<p class="form-success" role="status">{successMessage}</p>{/if}
      <button class="primary-button" type="submit" disabled={saving}>{saving ? "Securing key…" : status?.localVaultExists ? "Unlock and enrol key" : "Create and enrol key"}<span aria-hidden="true">→</span></button>
    </form>
    {#if status?.localVaultExists}
      <div class="key-replacement">
        <div><p class="section-label">Lost passphrase or planned rotation</p><strong>Replace this device key</strong></div>
        {#if confirmingReplacement}
          <div class="key-replacement-confirmation" role="alert">
            <p>{localKey?.signatureCount
              ? `This key has ${localKey.signatureCount} existing signatures. They will remain valid under time-scoped rotation, but this device's private key will be permanently deleted.`
              : "This key has not signed any assertions. Its audit entry will be retired and the unrecoverable private key will be permanently deleted without affecting clinical records."}</p>
            <div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={replacing} onclick={() => confirmingReplacement = false}>Keep key</button><button class="danger-button" type="button" disabled={replacing} onclick={() => void replaceKey()}>{replacing ? "Replacing…" : "Retire and delete local key"}</button></div>
          </div>
        {:else}
          <button class="secondary-button" type="button" onclick={() => confirmingReplacement = true}>Replace key…</button>
        {/if}
      </div>
    {/if}
  </article>
</section>
