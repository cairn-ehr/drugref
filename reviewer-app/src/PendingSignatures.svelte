<script lang="ts">
  /** Resumable queue for live curated revisions that remain unsigned. */

  import { onMount } from "svelte";
  import ReviewSigning from "./ReviewSigning.svelte";
  import { loadReviewDecision } from "./lib/decisions";
  import { workingRecordDate } from "./lib/presentation";
  import { loadPendingReviewSignatures } from "./lib/signing";
  import type {
    PendingReviewSignature,
    ReviewDecisionRecord,
  } from "./lib/types";

  let pending = $state<PendingReviewSignature[]>([]);
  let selected = $state<PendingReviewSignature | null>(null);
  let record = $state<ReviewDecisionRecord | null>(null);
  let loading = $state(true);
  let recordLoading = $state(false);
  let errorMessage = $state("");

  onMount((): void => void refresh());

  /** Reload the database-derived unsigned queue and retain a stable selection. */
  async function refresh(): Promise<void> {
    loading = true;
    errorMessage = "";
    try {
      pending = await loadPendingReviewSignatures();
      selected =
        pending.find((item): boolean => item.targetKey === selected?.targetKey) ??
        pending[0] ??
        null;
      if (selected) await loadSelected(selected);
      else record = null;
    } catch (error) {
      errorMessage = String(error);
    } finally {
      loading = false;
    }
  }

  /** Load complete immutable history for the selected resumable revision. */
  async function loadSelected(item: PendingReviewSignature): Promise<void> {
    selected = item;
    record = null;
    recordLoading = true;
    errorMessage = "";
    try {
      record = await loadReviewDecision({ kind: item.kind, targetKey: item.targetKey });
    } catch (error) {
      errorMessage = String(error);
    } finally {
      recordLoading = false;
    }
  }

  /** Remove a newly signed row by refreshing the authoritative resume queue. */
  function signed(updated: ReviewDecisionRecord): void {
    record = updated;
    void refresh();
  }

  /** Render database decision spellings as compact human labels. */
  function label(value: string): string {
    return value.replaceAll("_", " ");
  }
</script>

<section class="pending-layout">
  <div class="pending-list-panel">
    <div class="admin-card-head"><div><p class="section-label">Detached workflow</p><h2>Pending signatures</h2></div><span class="count-pill">{pending.length}</span></div>
    {#if loading}<p class="admin-empty">Loading unsigned revisions…</p>{/if}
    {#if errorMessage}<div class="record-error" role="alert"><span>{errorMessage}</span><button type="button" onclick={() => void refresh()}>Try again</button></div>{/if}
    <div class="pending-list">
      {#each pending as item (`${item.kind}-${item.revisionId}`)}
        <button class="pending-row" class:active={selected?.kind === item.kind && selected?.revisionId === item.revisionId} type="button" onclick={() => void loadSelected(item)}>
          <span><strong>{item.subjectName}</strong><small>{item.objectName}</small></span>
          <span><b>{label(item.decision)}</b><small>#{item.revisionId} · {workingRecordDate(item.reviewedAt)}</small></span>
        </button>
      {:else}
        {#if !loading}<p class="admin-empty">Every current GUI revision has at least one detached signature.</p>{/if}
      {/each}
    </div>
  </div>

  <article class="pending-detail-panel">
    {#if selected && record}
      <p class="section-label">Resume sign-off</p><h2>{selected.subjectName}</h2><p class="pending-object">{selected.objectName}</p>
      <p class="pending-attribution">{label(selected.decision)} · recorded by {selected.reviewedBy} at {workingRecordDate(selected.reviewedAt)}</p>
      <ReviewSigning item={selected} {record} onSigned={signed} />
    {:else if recordLoading}
      <p class="admin-empty">Loading decision history…</p>
    {:else}
      <p class="admin-empty">Choose an unsigned revision to resume signing.</p>
    {/if}
  </article>
</section>
