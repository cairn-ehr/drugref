<script lang="ts">
  /** Transactional clinical-decision form and immutable revision history. */

  import { onDestroy, onMount } from "svelte";
  import { CLINICAL_PROSE_MAX_LENGTH } from "./lib/constants";
  import ReviewSigning from "./ReviewSigning.svelte";
  import { createReviewDecision, loadReviewDecision } from "./lib/decisions";
  import { workingRecordDate } from "./lib/presentation";
  import type {
    EvidenceGrade,
    ReviewDecision,
    ReviewDecisionRecord,
    ReviewQueueItem,
    Severity,
  } from "./lib/types";

  /** Ordered severity choices shared with the curated overlay. */
  const SEVERITIES: Severity[] = ["contraindicated", "major", "moderate", "minor"];

  /** Ordered evidence-attestation choices shared with the curated overlay. */
  const EVIDENCE_GRADES: EvidenceGrade[] = [
    "established",
    "probable",
    "suspected",
    "theoretical",
  ];

  let {
    item,
    onRecorded,
  }: { item: ReviewQueueItem; onRecorded: (targetKey: string) => void } = $props();
  let record = $state<ReviewDecisionRecord | null>(null);
  let loading = $state(false);
  let saving = $state(false);
  let previewing = $state(false);
  let errorMessage = $state("");
  let decision = $state<ReviewDecision>(defaultDecision());
  let severity = $state<Severity>("major");
  let evidenceGrade = $state<EvidenceGrade>("established");
  let mechanism = $state("");
  let management = $state("");
  let mounted = true;

  let requiresGrade = $derived(decision !== "does_not_apply" && decision !== "spurious");

  onMount((): void => void refresh());
  onDestroy((): void => {
    mounted = false;
  });

  /** Return the safest initial choice for this target's distinct decision vocabulary. */
  function defaultDecision(): ReviewDecision {
    return item.kind === "interaction_rule" ? "applies" : "context_dependent";
  }

  /** Load immutable decision history and initialise corrections from the live row. */
  async function refresh(): Promise<void> {
    loading = true;
    errorMessage = "";
    previewing = false;
    try {
      const loaded = await loadReviewDecision({ kind: item.kind, targetKey: item.targetKey });
      if (!mounted) return;
      record = loaded;
      const current = loaded.history.find(
        (revision): boolean => revision.revisionId === loaded.currentRevisionId,
      );
      if (current) {
        decision = current.decision;
        severity = current.severity ?? "major";
        evidenceGrade = current.evidenceGrade ?? "established";
        mechanism = current.mechanism ?? "";
        management = current.management ?? "";
      }
    } catch (error) {
      if (mounted) errorMessage = String(error);
    } finally {
      if (mounted) loading = false;
    }
  }

  /** Move a complete draft into the explicit immutable-revision preview step. */
  function previewRevision(event: SubmitEvent): void {
    event.preventDefault();
    if (!record) return;
    previewing = true;
    errorMessage = "";
  }

  /** Record the previewed revision with an optimistic predecessor check. */
  async function recordRevision(): Promise<void> {
    if (!record) return;
    saving = true;
    errorMessage = "";
    try {
      const updated = await createReviewDecision({
        kind: item.kind,
        targetKey: item.targetKey,
        decision,
        severity: requiresGrade ? severity : undefined,
        mechanism,
        management,
        evidenceGrade: requiresGrade ? evidenceGrade : undefined,
        expectedRevisionId: record.currentRevisionId,
      }, {
        subjectName: item.subjectName,
        objectName: item.objectName,
      });
      if (!mounted) return;
      record = updated;
      previewing = false;
      onRecorded(item.targetKey);
    } catch (error) {
      if (mounted) errorMessage = String(error);
    } finally {
      if (mounted) saving = false;
    }
  }

  /** Render a compact human label for a wire decision or grade. */
  function label(value: string): string {
    return value.replaceAll("_", " ");
  }

  /** Replace local history with the service response after verified signing. */
  function signatureRecorded(updated: ReviewDecisionRecord): void {
    record = updated;
  }
</script>

<div class="section-heading"><div><p class="section-label">Clinical judgement</p><h3>Append-only decision</h3></div><span>{record?.history.length ?? 0} revisions</span></div>
{#if loading}<p class="record-status">Loading decision history…</p>{/if}
{#if errorMessage}<div class="record-error" role="alert"><span>{errorMessage}</span><button type="button" onclick={() => void refresh()}>Reload history</button></div>{/if}

{#if record?.history.length}
  <div class="decision-history">
    {#each record.history as revision (revision.revisionId)}
      <article class="decision-revision" class:decision-revision--current={revision.revisionId === record.currentRevisionId}>
        <div><strong>{label(revision.decision)}</strong><span>{revision.revisionId === record.currentRevisionId ? "Current" : `Superseded by #${revision.supersededBy}`} · {revision.signatureStatus}</span></div>
        <p>{revision.severity ? `${label(revision.severity)} · ${label(revision.evidenceGrade ?? "")}` : "Reviewed as non-applying / spurious"}</p>
        {#if revision.mechanism}<small><b>Mechanism</b> {revision.mechanism}</small>{/if}
        {#if revision.management}<small><b>Management</b> {revision.management}</small>{/if}
        <footer>{revision.reviewedBy} · {workingRecordDate(revision.reviewedAt)} · against {revision.reviewedAgainst}</footer>
      </article>
    {/each}
  </div>
{:else if !loading}
  <p class="record-empty">No clinical decision has been recorded for this target.</p>
{/if}

{#if record}
  <ReviewSigning {item} {record} onSigned={signatureRecorded} />
{/if}

<form class="decision-grid" onsubmit={previewRevision}>
  <label><span>Ruling</span><select bind:value={decision} disabled={loading || saving || !record}>
    {#if item.kind === "interaction_rule"}
      <option value="applies">Applies</option><option value="does_not_apply">Does not apply</option>
    {:else}
      <option value="contraindicated">Contraindicated</option><option value="indicated">Indicated</option><option value="context_dependent">Context dependent</option><option value="spurious">Spurious</option>
    {/if}
  </select></label>
  <label><span>Severity</span><select bind:value={severity} required={requiresGrade} disabled={loading || saving || !record || !requiresGrade}>{#each SEVERITIES as value}<option value={value}>{label(value)}</option>{/each}</select></label>
  <label class="wide"><span>Mechanism</span><textarea maxlength={CLINICAL_PROSE_MAX_LENGTH} disabled={loading || saving || !record} bind:value={mechanism} placeholder="Explain the clinical mechanism"></textarea></label>
  <label class="wide"><span>Management</span><textarea maxlength={CLINICAL_PROSE_MAX_LENGTH} disabled={loading || saving || !record} bind:value={management} placeholder="Describe practical management"></textarea></label>
  <label><span>Evidence grade</span><select bind:value={evidenceGrade} required={requiresGrade} disabled={loading || saving || !record || !requiresGrade}>{#each EVIDENCE_GRADES as value}<option value={value}>{label(value)}</option>{/each}</select></label>
  <label><span>Reviewed against</span><input disabled value={item.upstreamReleases.join(" / ")} /></label>
  <button class="secondary-button decision-preview-button" type="submit" disabled={loading || saving || !record}>Preview revision</button>
</form>

{#if previewing && record}
  <article class="decision-preview" aria-live="polite">
    <div><p class="section-label">Immutable revision preview</p><strong>{label(decision)}</strong><span>{requiresGrade ? `${label(severity)} · ${label(evidenceGrade)}` : "No severity or evidence grade"}</span></div>
    {#if mechanism.trim()}<small><b>Mechanism</b> {mechanism.trim()}</small>{/if}
    {#if management.trim()}<small><b>Management</b> {management.trim()}</small>{/if}
    <small><b>Reviewed against</b> {item.upstreamReleases.join(" / ")}</small>
    <p>{record.currentRevisionId ? `This will supersede revision #${record.currentRevisionId}.` : "This will be the first clinical revision for this target."} Signing remains a separate action.</p>
    <div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={saving} onclick={() => previewing = false}>Keep editing</button><button class="primary-button primary-button--compact" type="button" disabled={saving} onclick={() => void recordRevision()}>{saving ? "Recording…" : "Record revision"}</button></div>
  </article>
{/if}
