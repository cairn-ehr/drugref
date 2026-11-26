<script lang="ts">
  /**
   * Departed graded pairs (#283): reviewed grades that stopped reaching drugs they
   * once graded (a drug left a class, or a policy cut a subclass). A departure keeps
   * its CHANGED SINCE REVIEW banner until a reviewer acknowledges it here or with
   * `drugref curate acknowledge-departure`; both call the same SQL rule (db/087).
   */

  import { onMount } from "svelte";
  import { DEPARTURE_RATIONALE_MAX_LENGTH } from "./lib/constants";
  import { acknowledgeGradedPairDeparture, loadGradedPairDepartures } from "./lib/departures";
  import { workingRecordDate } from "./lib/presentation";
  import type { DepartureLeaver, GradedPairDeparture } from "./lib/types";

  let departures = $state<GradedPairDeparture[]>([]);
  let selected = $state<GradedPairDeparture | null>(null);
  let rationale = $state("");
  let loading = $state(true);
  let saving = $state(false);
  let errorMessage = $state("");
  let statusMessage = $state("");

  onMount((): void => void refresh());

  /** Stable identity of one departure across reloads. */
  function keyOf(departure: GradedPairDeparture): string {
    return `${departure.ruleGrain}-${departure.rulingId}`;
  }

  /** Reload the authoritative list and keep the selection when it still exists. */
  async function refresh(): Promise<void> {
    loading = true;
    errorMessage = "";
    try {
      departures = await loadGradedPairDepartures();
      const current = selected ? keyOf(selected) : "";
      selected = departures.find((item): boolean => keyOf(item) === current) ?? departures[0] ?? null;
    } catch (error) {
      errorMessage = String(error);
    } finally {
      loading = false;
    }
  }

  /** Acknowledge one leaver of the selected ruling, then reload the list. */
  async function acknowledge(departure: GradedPairDeparture, leaver: DepartureLeaver): Promise<void> {
    saving = true;
    errorMessage = "";
    statusMessage = "";
    try {
      const done = await acknowledgeGradedPairDeparture({
        ruleGrain: departure.ruleGrain,
        rulingId: departure.rulingId,
        moietyUuid: leaver.moietyUuid,
        rationale,
      });
      statusMessage = `Acknowledged ${leaver.displayName}: closed ${done.closedPairs} ${done.closedPairs === 1 ? "pair" : "pairs"}, recorded against ${done.reviewedAgainst}.`;
      rationale = "";
      await refresh();
    } catch (error) {
      errorMessage = String(error);
    } finally {
      saving = false;
    }
  }

  /** Render database spellings as compact human labels. */
  function label(value: string): string {
    return value.replaceAll("_", " ");
  }
</script>

<section class="pending-layout">
  <div class="pending-list-panel">
    <div class="admin-card-head"><div><p class="section-label">Changed since review</p><h2>Departed grades</h2></div><span class="count-pill">{departures.length}</span></div>
    {#if loading}<p class="admin-empty">Loading departures…</p>{/if}
    {#if errorMessage}<div class="record-error" role="alert"><span>{errorMessage}</span><button type="button" onclick={() => void refresh()}>Try again</button></div>{/if}
    <div class="pending-list">
      {#each departures as item (keyOf(item))}
        <button class="pending-row" class:active={selected !== null && keyOf(selected) === keyOf(item)} type="button" onclick={() => { selected = item; statusMessage = ""; }}>
          <span><strong>{item.subjectName}</strong><small>{item.objectClassName}</small></span>
          <span><b>{label(item.severity)}</b><small>{label(item.ruleGrain)} #{item.rulingId} · {item.pairCount} {item.pairCount === 1 ? "pair" : "pairs"}</small></span>
        </button>
      {:else}
        {#if !loading}<p class="admin-empty">Every reviewed grade still reaches every pair it was observed grading.</p>{/if}
      {/each}
    </div>
  </div>

  <article class="pending-detail-panel">
    {#if statusMessage}<p class="record-status" role="status">{statusMessage}</p>{/if}
    {#if selected}
      <p class="section-label">{label(selected.ruleGrain)} #{selected.rulingId} · {selected.relationship}</p>
      <h2>{selected.subjectName}</h2><p class="pending-object">{selected.objectClassName}</p>
      <p class="pending-attribution departure-attribution">{label(selected.severity)} · reviewed by {selected.reviewedBy} against {selected.reviewedAgainst} · first seen {workingRecordDate(selected.firstSeenAt)}{selected.backfilled ? " (reconstructed at db/083's migration)" : ""}</p>
      <p class="admin-intro departure-intro">This ruling no longer reaches {selected.pairCount} {selected.pairCount === 1 ? "pair" : "pairs"} it graded. Acknowledge a drug only if its departure is clinically correct: the warning then stops showing for every listed pair that names it. It is recorded under your name, against the ingest release(s) that hold the ruling's classes now.</p>
      <form class="decision-grid" onsubmit={(event) => event.preventDefault()}>
        <label class="wide"><span>Rationale</span><textarea maxlength={DEPARTURE_RATIONALE_MAX_LENGTH} disabled={saving} bind:value={rationale} placeholder="Why the departure is correct (for example: reclassified upstream)"></textarea></label>
      </form>
      {#each selected.leavers as leaver (leaver.moietyUuid)}
        <div class="decision-preview-actions departure-leaver">
          <span>{leaver.displayName} left · closes {leaver.closes} {leaver.closes === 1 ? "pair" : "pairs"}</span>
          <button class="primary-button primary-button--compact" type="button" disabled={saving || rationale.trim() === ""} onclick={() => selected && void acknowledge(selected, leaver)}>{saving ? "Recording…" : `Acknowledge ${leaver.displayName}`}</button>
        </div>
      {/each}
    {:else if !loading}
      <p class="admin-empty">Nothing to acknowledge.</p>
    {/if}
  </article>
</section>
