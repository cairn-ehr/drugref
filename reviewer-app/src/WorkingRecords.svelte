<script lang="ts">
  /** Append-only working-history panel for one selected review target. */

  import { onDestroy, onMount } from "svelte";
  import {
    ANNOTATION_MAX_LENGTH,
    EVIDENCE_NOTE_MAX_LENGTH,
    EVIDENCE_REFERENCE_MAX_LENGTH,
  } from "./lib/constants";
  import { workingRecordDate } from "./lib/presentation";
  import {
    createEvidenceReference,
    createReviewAnnotation,
    loadReviewRecord,
  } from "./lib/records";
  import type {
    EvidenceReferenceScheme,
    ReviewQueueItem,
    ReviewRecord,
  } from "./lib/types";

  /** Evidence-reference schemes shared with db/045 and the reviewer domain. */
  const EVIDENCE_SCHEMES: EvidenceReferenceScheme[] = ["DOI", "PMID", "PMCID", "NCT", "SPL", "URL"];

  let { item }: { item: ReviewQueueItem } = $props();
  let reviewRecord = $state<ReviewRecord | null>(null);
  let loading = $state(false);
  let saving = $state(false);
  let errorMessage = $state("");
  let annotationMarkdown = $state("");
  let referenceScheme = $state<EvidenceReferenceScheme>("PMID");
  let referenceValue = $state("");
  let referenceNote = $state("");
  let mounted = true;

  onMount((): void => void refresh());
  onDestroy((): void => {
    mounted = false;
  });

  /** Load immutable working history for the component's stable keyed target. */
  async function refresh(): Promise<void> {
    loading = true;
    errorMessage = "";
    try {
      const loaded = await loadReviewRecord({ kind: item.kind, targetKey: item.targetKey });
      if (mounted) reviewRecord = loaded;
    } catch (error) {
      if (mounted) errorMessage = String(error);
    } finally {
      if (mounted) loading = false;
    }
  }

  /** Append a Markdown note without changing question state or clinical data. */
  async function saveAnnotation(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!reviewRecord || !annotationMarkdown.trim()) return;
    saving = true;
    errorMessage = "";
    try {
      const created = await createReviewAnnotation({
        kind: item.kind,
        targetKey: item.targetKey,
        annotationMarkdown,
      });
      if (!mounted) return;
      const current = reviewRecord;
      reviewRecord = { ...current, annotations: [...current.annotations, created] };
      annotationMarkdown = "";
    } catch (error) {
      if (mounted) errorMessage = String(error);
    } finally {
      if (mounted) saving = false;
    }
  }

  /** Append a citation-only reference without asserting a verdict or grade. */
  async function saveEvidenceReference(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (!reviewRecord || !referenceValue.trim()) return;
    saving = true;
    errorMessage = "";
    try {
      const created = await createEvidenceReference({
        kind: item.kind,
        targetKey: item.targetKey,
        referenceScheme,
        referenceValue,
        noteMarkdown: referenceNote,
      });
      if (!mounted) return;
      const current = reviewRecord;
      reviewRecord = {
        ...current,
        evidenceReferences: [...current.evidenceReferences, created],
      };
      referenceValue = "";
      referenceNote = "";
    } catch (error) {
      if (mounted) errorMessage = String(error);
    } finally {
      if (mounted) saving = false;
    }
  }

</script>

<div class="section-heading"><div><p class="section-label">Reviewer annotations</p><h3>Append-only working notes</h3></div><span>{reviewRecord?.annotations.length ?? 0} recorded</span></div>
{#if loading}<p class="record-status">Loading working history…</p>{/if}
{#if errorMessage}<div class="record-error" role="alert"><span>{errorMessage}</span><button type="button" onclick={() => void refresh()}>Try again</button></div>{/if}
{#if reviewRecord?.annotations.length}
  <div class="working-record-list">
    {#each reviewRecord.annotations as annotation (annotation.annotationId)}
      <article class="working-record-card"><div><strong>{annotation.reviewerName}</strong><span>@{annotation.username} · {workingRecordDate(annotation.recordedAt)}</span></div><p>{annotation.annotationMarkdown}</p></article>
    {/each}
  </div>
{:else if !loading}
  <p class="record-empty">No reviewer notes have been recorded for this target.</p>
{/if}
<form class="working-record-form" onsubmit={saveAnnotation}>
  <label for="annotation-markdown">New working note</label>
  <textarea id="annotation-markdown" class="annotation" required maxlength={ANNOTATION_MAX_LENGTH} disabled={loading || saving || !reviewRecord} bind:value={annotationMarkdown} placeholder="Add an evidence-grounded Markdown note…"></textarea>
  <button class="secondary-button record-submit" type="submit" disabled={loading || saving || !reviewRecord || !annotationMarkdown.trim()}>{saving ? "Saving…" : "Save annotation"}</button>
</form>

<div class="section-heading"><div><p class="section-label">Evidence references</p><h3>Citation-only research trail</h3></div><span>{reviewRecord?.evidenceReferences.length ?? 0} attached</span></div>
{#if reviewRecord?.evidenceReferences.length}
  <div class="working-record-list">
    {#each reviewRecord.evidenceReferences as reference (reference.evidenceReferenceId)}
      <article class="working-record-card working-record-card--reference"><div><strong>{reference.referenceScheme} {reference.referenceValue}</strong><span>{reference.reviewerName} · {workingRecordDate(reference.recordedAt)}</span></div>{#if reference.noteMarkdown}<p>{reference.noteMarkdown}</p>{/if}</article>
    {/each}
  </div>
{:else if !loading}
  <p class="record-empty">No evidence references have been attached. A reference alone does not assert a verdict.</p>
{/if}
<form class="evidence-form" onsubmit={saveEvidenceReference}>
  <label><span>Scheme</span><select bind:value={referenceScheme} disabled={loading || saving || !reviewRecord}>{#each EVIDENCE_SCHEMES as scheme}<option value={scheme}>{scheme}</option>{/each}</select></label>
  <label class="evidence-value"><span>Identifier or URL</span><input required maxlength={EVIDENCE_REFERENCE_MAX_LENGTH} disabled={loading || saving || !reviewRecord} bind:value={referenceValue} placeholder="10.1000/example or 12345678" /></label>
  <label class="evidence-note"><span>Context (optional Markdown)</span><textarea maxlength={EVIDENCE_NOTE_MAX_LENGTH} disabled={loading || saving || !reviewRecord} bind:value={referenceNote} placeholder="Why this source is relevant to the review"></textarea></label>
  <button class="secondary-button" type="submit" disabled={loading || saving || !reviewRecord || !referenceValue.trim()}>Attach reference</button>
</form>
