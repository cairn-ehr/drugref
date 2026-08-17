<script lang="ts">
  import type { ReviewItem, ReviewKind, ReviewWorkspace } from "./lib/types";
  import { loadReviewWorkspace } from "./lib/workspace";

  let workspace = $state<ReviewWorkspace | null>(null);
  let username = $state("maya.chen");
  let password = $state("preview");
  let loginError = $state("");
  let loading = $state(false);
  let selectedId = $state("");
  let search = $state("");
  let kindFilter = $state<"all" | ReviewKind>("all");
  let stateFilter = $state("all");

  let visibleItems = $derived.by(() => {
    if (!workspace) return [];
    const needle = search.trim().toLocaleLowerCase();
    return workspace.items.filter((item: ReviewItem) => {
      const matchesKind = kindFilter === "all" || item.kind === kindFilter;
      const matchesState = stateFilter === "all" || item.reviewState === stateFilter;
      const matchesSearch =
        !needle ||
        item.subjectName.toLocaleLowerCase().includes(needle) ||
        item.objectName.toLocaleLowerCase().includes(needle) ||
        item.relationship.toLocaleLowerCase().includes(needle);
      return matchesKind && matchesState && matchesSearch;
    });
  });

  let selectedItem = $derived(
    workspace?.items.find((item: ReviewItem) => item.id === selectedId) ?? visibleItems[0] ?? null,
  );

  async function login(event: SubmitEvent) {
    event.preventDefault();
    loginError = "";
    if (!username.trim() || !password) {
      loginError = "Enter a username and password to open the preview.";
      return;
    }

    loading = true;
    try {
      workspace = await loadReviewWorkspace();
      selectedId = workspace.items[0]?.id ?? "";
    } catch (error) {
      loginError = `Could not load the review workspace: ${String(error)}`;
    } finally {
      loading = false;
    }
  }

  function signOut() {
    workspace = null;
    password = "preview";
    selectedId = "";
  }

  function kindLabel(kind: ReviewKind) {
    return kind === "interaction_rule" ? "Interaction rule" : "Condition conflict";
  }

  function initials(fullName: string) {
    return fullName
      .replace(/^Dr\s+/u, "")
      .split(/\s+/u)
      .map((part) => part[0])
      .join("")
      .slice(0, 2)
      .toUpperCase();
  }
</script>

<svelte:head>
  <meta
    name="description"
    content="Human review interface for Drugref's signed, append-only curated overlay"
  />
</svelte:head>

{#if !workspace}
  <main class="login-shell">
    <section class="login-story" aria-labelledby="product-name">
      <div class="brand brand--light">
        <span class="brand-mark" aria-hidden="true">dr</span>
        <span id="product-name">drugref</span>
      </div>
      <div class="story-copy">
        <p class="eyebrow">Clinical review workspace</p>
        <h1>Every decision should remain explainable.</h1>
        <p>
          Inspect source assertions, record clinical judgement, attach evidence, and
          sign exactly what you reviewed.
        </p>
      </div>
      <div class="principle-grid" aria-label="Drugref review principles">
        <div><strong>Append-only</strong><span>Corrections preserve their history.</span></div>
        <div><strong>Human verified</strong><span>Clinical interpretation stays with clinicians.</span></div>
        <div><strong>Cryptographically signed</strong><span>Private keys remain with reviewers.</span></div>
      </div>
      <p class="story-foot">drugref.org · global reference tier</p>
    </section>

    <section class="login-panel" aria-labelledby="sign-in-title">
      <div class="preview-pill"><span></span> Interface preview</div>
      <div class="login-card">
        <p class="eyebrow eyebrow--ink">Reviewer access</p>
        <h2 id="sign-in-title">Sign in to your workspace</h2>
        <p class="login-intro">
          This slice demonstrates the reviewer experience. The authentication service is
          not connected and no clinical data can be changed.
        </p>

        <form onsubmit={login}>
          <label for="username">Username</label>
          <input id="username" autocomplete="username" bind:value={username} />
          <div class="field-heading">
            <label for="password">Password</label><span>Preview value is prefilled</span>
          </div>
          <input id="password" type="password" autocomplete="current-password" bind:value={password} />
          {#if loginError}<p class="form-error" role="alert">{loginError}</p>{/if}
          <button class="primary-button" type="submit" disabled={loading}>
            {loading ? "Opening workspace…" : "Open review workspace"}<span aria-hidden="true">→</span>
          </button>
        </form>

        <div class="security-note">
          <span class="lock" aria-hidden="true"></span>
          <p>
            Production passwords will be Argon2id-hashed by the service. Signing keys
            will remain encrypted on this device.
          </p>
        </div>
      </div>
    </section>
  </main>
{:else}
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand brand--sidebar">
        <span class="brand-mark" aria-hidden="true">dr</span><span>drugref</span>
      </div>
      <nav aria-label="Primary navigation">
        <p class="nav-label">Workspace</p>
        <button class="nav-item active" type="button">
          <span class="nav-icon" aria-hidden="true">⌁</span>Review queue
          <span class="nav-count">
            {workspace.summary.interactionRules + workspace.summary.conditionContradictions}
          </span>
        </button>
        <button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">✓</span>Reviewed entries</button>
        <button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">⌕</span>Evidence library</button>
        <p class="nav-label nav-label--spaced">Administration</p>
        <button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">♙</span>Reviewers</button>
        <button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">◇</span>Signing keys</button>
      </nav>
      <div class="sidebar-status">
        <div class="status-row"><span class="status-dot"></span><span>Preview dataset</span></div>
        <small>Snapshot {workspace.generatedAt.slice(0, 10)}</small>
      </div>
    </aside>

    <main class="workspace-shell">
      <header class="topbar">
        <div><p class="eyebrow eyebrow--ink">Clinical curation</p><h1>Review queue</h1></div>
        <div class="topbar-actions">
          <span class="preview-pill"><span></span> Read-only preview</span>
          <div class="reviewer-chip">
            <span class="avatar">{initials(workspace.reviewer.fullName)}</span>
            <span><strong>{workspace.reviewer.fullName}</strong><small>{workspace.reviewer.qualifications}</small></span>
          </div>
          <button class="icon-button" type="button" aria-label="Sign out" onclick={signOut}>↗</button>
        </div>
      </header>

      <section class="metrics" aria-label="Review queue summary">
        <article><span class="metric-icon metric-icon--amber" aria-hidden="true">↔</span><div><strong>{workspace.summary.interactionRules}</strong><span>Interaction rules</span></div><small>Awaiting judgement</small></article>
        <article><span class="metric-icon metric-icon--rose" aria-hidden="true">±</span><div><strong>{workspace.summary.conditionContradictions}</strong><span>Condition conflicts</span></div><small>Need clinical context</small></article>
        <article><span class="metric-icon metric-icon--green" aria-hidden="true">✓</span><div><strong>{workspace.summary.reviewedPairs}</strong><span>Curated DDI pairs</span></div><small>Expanded from signed rules</small></article>
      </section>

      <section class="review-layout">
        <div class="queue-panel">
          <div class="queue-tools">
            <label class="search-box" aria-label="Search review queue">
              <span aria-hidden="true">⌕</span><input placeholder="Search drug, class, or relationship" bind:value={search} />
            </label>
            <div class="filter-row">
              <label><span>Type</span><select bind:value={kindFilter}><option value="all">All review types</option><option value="interaction_rule">Interactions</option><option value="condition_contradiction">Condition conflicts</option></select></label>
              <label><span>Status</span><select bind:value={stateFilter}><option value="all">All statuses</option><option value="unreviewed">Unreviewed</option><option value="in_review">In review</option></select></label>
            </div>
          </div>
          <div class="queue-heading"><span>{visibleItems.length} representative records</span><span>Impact</span></div>
          <div class="queue-list">
            {#each visibleItems as item (item.id)}
              <button class="queue-item" class:selected={selectedItem?.id === item.id} type="button" onclick={() => (selectedId = item.id)}>
                <span class="queue-main">
                  <span class="queue-badges">
                    <span class:conflict={item.kind === "condition_contradiction"}>{kindLabel(item.kind)}</span>
                    {#if item.reviewState === "in_review"}<span class="in-review">In review</span>{/if}
                  </span>
                  <strong>{item.subjectName}</strong><span class="object-name">{item.objectName}</span>
                  <small>{item.relationship} · {item.candidateSource}</small>
                </span>
                <span class="impact-count" class:high={item.priority === "high"}>{item.impactCount}<small>{item.kind === "interaction_rule" ? "pairs" : "conflict"}</small></span>
              </button>
            {:else}<div class="empty-state">No representative records match these filters.</div>{/each}
          </div>
        </div>

        {#if selectedItem}
          <section class="detail-panel" aria-label={`Review details for ${selectedItem.subjectName}`}>
            <div class="detail-head">
              <div class="detail-kicker"><span>{kindLabel(selectedItem.kind)}</span><span>•</span><span>{selectedItem.relationship}</span></div>
              <h2>{selectedItem.subjectName}</h2><p class="relation-line"><span>with</span> {selectedItem.objectName}</p>
              <div class="detail-badges"><span class="badge badge--amber">Unsigned</span><span class="badge">{selectedItem.candidateSource}</span><span class="badge">Release {selectedItem.upstreamRelease}</span></div>
            </div>
            <div class="detail-scroll">
              <article class="question-card">
                <p class="section-label">Review question</p><p>{selectedItem.question}</p>
                <div class="impact-callout"><strong>{selectedItem.impactCount}</strong><span>{selectedItem.kind === "interaction_rule" ? "candidate pairs inherit this one rule" : "stable pair carries contradictory projections"}</span></div>
              </article>
              <div class="section-heading"><div><p class="section-label">Clinical judgement</p><h3>Decision fields</h3></div><span>Write path arrives next</span></div>
              <div class="decision-grid" aria-disabled="true">
                <label><span>Ruling</span><select disabled><option>Choose a ruling…</option></select></label>
                <label><span>Severity</span><select disabled><option>Choose severity…</option></select></label>
                <label class="wide"><span>Mechanism</span><textarea disabled placeholder="Explain the clinical mechanism"></textarea></label>
                <label class="wide"><span>Management</span><textarea disabled placeholder="Describe practical management"></textarea></label>
                <label><span>Evidence grade</span><select disabled><option>Choose evidence…</option></select></label>
                <label><span>Reviewed against</span><input disabled value={selectedItem.upstreamRelease} /></label>
              </div>
              <div class="section-heading"><div><p class="section-label">Provenance</p><h3>Why this is in the queue</h3></div></div>
              <article class="provenance-card">
                <div class="provenance-line"><span>Source assertion</span><strong>{selectedItem.candidateSource}</strong></div><p>{selectedItem.provenance}</p>
                <dl><div><dt>Subject UUID</dt><dd>{selectedItem.subjectUuid}</dd></div><div><dt>Object UUID</dt><dd>{selectedItem.objectUuid}</dd></div></dl>
              </article>
              <div class="section-heading"><div><p class="section-label">Reviewer annotation</p><h3>Working note</h3></div></div>
              <textarea class="annotation" disabled placeholder="Add an evidence-grounded Markdown note…"></textarea>
            </div>
            <footer class="detail-actions">
              <div><span class="key-indicator"><span></span> Ed25519 key enrolled</span><small title={workspace.reviewer.keyFingerprint}>{workspace.reviewer.keyFingerprint.slice(0, 12)}…</small></div>
              <button class="secondary-button" type="button" disabled>Save annotation</button>
              <button class="primary-button primary-button--compact" type="button" disabled>Record &amp; sign decision</button>
            </footer>
          </section>
        {:else}<div class="detail-panel empty-state">Choose a record to inspect it.</div>{/if}
      </section>
    </main>
  </div>
{/if}
