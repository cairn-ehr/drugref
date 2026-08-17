<script lang="ts">
  import { onMount } from "svelte";
  import UserManagement from "./UserManagement.svelte";
  import {
    accountMode,
    bootstrapAdmin,
    login as authenticate,
    logout,
    startupState,
    type CreateAccountInput,
    type ReviewerAccount,
  } from "./lib/accounts";
  import type { ReviewKind, ReviewQueueItem, ReviewQueuePage } from "./lib/types";
  import { loadReviewQueue } from "./lib/workspace";

  type Screen = "checking" | "bootstrap" | "login" | "workspace" | "unavailable";
  type View = "queue" | "users";

  let screen = $state<Screen>("checking");
  let activeView = $state<View>("queue");
  let workspace = $state<ReviewQueuePage | null>(null);
  let currentUser = $state<ReviewerAccount | null>(null);
  let username = $state(accountMode() === "browser-preview" ? "maya.chen" : "");
  let password = $state(accountMode() === "browser-preview" ? "preview" : "");
  let authError = $state("");
  let loading = $state(false);
  let selectedId = $state("");
  let search = $state("");
  let kindFilter = $state<"all" | ReviewKind>("all");
  let sourceFilter = $state("all");
  let relationshipFilter = $state("all");
  let queueLoading = $state(false);
  let queueError = $state("");
  let queueRequest = 0;
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  let bootstrapInput = $state<CreateAccountInput>({
    username: "",
    fullName: "",
    qualifications: "",
    bioMarkdown: "",
    role: "administrator",
    password: "",
  });
  let bootstrapConfirmation = $state("");

  let selectedItem = $derived(
    workspace?.items.find((item: ReviewQueueItem) => item.id === selectedId) ?? workspace?.items[0] ?? null,
  );

  onMount(() => void checkStartup());

  async function checkStartup() {
    screen = "checking";
    authError = "";
    try {
      const state = await startupState();
      screen = state.bootstrapRequired ? "bootstrap" : "login";
    } catch (error) {
      authError = String(error);
      screen = "unavailable";
    }
  }

  async function submitLogin(event: SubmitEvent) {
    event.preventDefault();
    authError = "";
    loading = true;
    try {
      await openWorkspace(await authenticate(username, password));
    } catch (error) {
      authError = String(error);
    } finally {
      loading = false;
    }
  }

  async function submitBootstrap(event: SubmitEvent) {
    event.preventDefault();
    authError = "";
    if (bootstrapInput.password !== bootstrapConfirmation) {
      authError = "Passwords do not match.";
      return;
    }
    loading = true;
    try {
      await openWorkspace(await bootstrapAdmin(bootstrapInput));
    } catch (error) {
      authError = String(error);
    } finally {
      loading = false;
    }
  }

  async function openWorkspace(user: ReviewerAccount) {
    currentUser = user;
    try {
      await refreshQueue(1);
      activeView = "queue";
      screen = "workspace";
    } catch (error) {
      screen = "unavailable";
      throw new Error(`Account access succeeded, but the review workspace could not load: ${String(error)}`);
    }
  }

  async function refreshQueue(page = 1) {
    const request = ++queueRequest;
    queueLoading = true;
    queueError = "";
    try {
      const loaded = await loadReviewQueue({
        page,
        pageSize: 25,
        kind: kindFilter === "all" ? undefined : kindFilter,
        source: sourceFilter === "all" ? undefined : sourceFilter,
        relationship: relationshipFilter === "all" ? undefined : relationshipFilter,
        search: search.trim() || undefined,
      });
      if (request !== queueRequest) return;
      workspace = loaded;
      selectedId = loaded.items.some((item) => item.id === selectedId)
        ? selectedId
        : (loaded.items[0]?.id ?? "");
    } catch (error) {
      if (request !== queueRequest) return;
      queueError = String(error);
      if (!workspace) throw error;
    } finally {
      if (request === queueRequest) queueLoading = false;
    }
  }

  function applyFilters() {
    void refreshQueue(1);
  }

  function scheduleSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => void refreshQueue(1), 350);
  }

  function changePage(delta: number) {
    if (workspace) void refreshQueue(workspace.pagination.page + delta);
  }

  async function signOut() {
    try {
      await logout();
    } finally {
      workspace = null;
      currentUser = null;
      selectedId = "";
      queueRequest += 1;
      if (searchTimer) clearTimeout(searchTimer);
      password = accountMode() === "browser-preview" ? "preview" : "";
      screen = "login";
    }
  }

  function kindLabel(kind: ReviewKind) {
    return kind === "interaction_rule" ? "Interaction rule" : "Condition conflict";
  }

  function initials(fullName: string) {
    return fullName.replace(/^Dr\s+/u, "").split(/\s+/u).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  }
</script>

<svelte:head><meta name="description" content="Human review interface for Drugref's signed, append-only curated overlay" /></svelte:head>

{#if screen !== "workspace" || !workspace || !currentUser}
  <main class="login-shell">
    <section class="login-story" aria-labelledby="product-name">
      <div class="brand brand--light"><span class="brand-mark" aria-hidden="true">dr</span><span id="product-name">drugref</span></div>
      <div class="story-copy"><p class="eyebrow">Clinical review workspace</p><h1>Every decision should remain explainable.</h1><p>Inspect source assertions, record clinical judgement, attach evidence, and sign exactly what you reviewed.</p></div>
      <div class="principle-grid" aria-label="Drugref review principles"><div><strong>Append-only</strong><span>Corrections preserve their history.</span></div><div><strong>Human verified</strong><span>Clinical interpretation stays with clinicians.</span></div><div><strong>Cryptographically signed</strong><span>Private keys remain with reviewers.</span></div></div>
      <p class="story-foot">drugref.org · global reference tier</p>
    </section>

    <section class="login-panel" aria-live="polite">
      <div class="preview-pill"><span></span>{accountMode() === "browser-preview" ? "Browser preview" : "Secure account service"}</div>
      <div class="login-card" class:login-card--wide={screen === "bootstrap"}>
        {#if screen === "checking"}
          <p class="eyebrow eyebrow--ink">Starting Drugref Reviewer</p><h2>Checking account setup…</h2><p class="login-intro">The review workspace stays closed until the service confirms that an administrator exists.</p>
        {:else if screen === "unavailable"}
          <p class="eyebrow eyebrow--ink">Service unavailable</p><h2>Reviewer access could not be checked</h2><p class="login-intro">No workspace data has been loaded. Start the reviewer service and confirm migration 044 is applied.</p>
          <p class="form-error" role="alert">{authError}</p><button class="primary-button" type="button" onclick={checkStartup}>Try again<span aria-hidden="true">→</span></button>
        {:else if screen === "bootstrap"}
          <p class="eyebrow eyebrow--ink">First-run registration</p><h2 id="bootstrap-title">Create the first administrator</h2><p class="login-intro">No active administrator is registered. This account must be created before Drugref Reviewer can finish starting.</p>
          <form class="bootstrap-form" onsubmit={submitBootstrap} aria-labelledby="bootstrap-title">
            <div class="auth-form-row"><label><span>Username</span><input required minlength="3" maxlength="64" pattern="[a-z][a-z0-9._-]+" autocomplete="username" bind:value={bootstrapInput.username} /></label><label><span>Full name</span><input required maxlength="200" autocomplete="name" bind:value={bootstrapInput.fullName} /></label></div>
            <div class="auth-form-row"><label><span>Qualifications</span><input maxlength="500" bind:value={bootstrapInput.qualifications} /></label><label><span>Role</span><input value="Administrator" disabled /></label></div>
            <label><span>Brief biography <small>Markdown source</small></span><textarea maxlength="10000" bind:value={bootstrapInput.bioMarkdown}></textarea></label>
            <div class="auth-form-row"><label><span>Password</span><input type="password" required minlength="12" maxlength="256" autocomplete="new-password" bind:value={bootstrapInput.password} /></label><label><span>Confirm password</span><input type="password" required minlength="12" maxlength="256" autocomplete="new-password" bind:value={bootstrapConfirmation} /></label></div>
            {#if authError}<p class="form-error" role="alert">{authError}</p>{/if}
            <button class="primary-button" type="submit" disabled={loading}>{loading ? "Creating administrator…" : "Create administrator and continue"}<span aria-hidden="true">→</span></button>
          </form>
          <div class="security-note"><span class="lock" aria-hidden="true"></span><p>The password is sent only to the review service for Argon2id hashing. The bootstrap route closes as soon as an administrator exists.</p></div>
        {:else}
          <p class="eyebrow eyebrow--ink">Reviewer access</p><h2 id="sign-in-title">Sign in to your workspace</h2><p class="login-intro">Authentication authorises access to the service. Clinical sign-off remains a separate device-key action.</p>
          <form onsubmit={submitLogin} aria-labelledby="sign-in-title"><label for="username">Username</label><input id="username" required autocomplete="username" bind:value={username} /><label for="password">Password</label><input id="password" required type="password" autocomplete="current-password" bind:value={password} />{#if authError}<p class="form-error" role="alert">{authError}</p>{/if}<button class="primary-button" type="submit" disabled={loading}>{loading ? "Signing in…" : "Open review workspace"}<span aria-hidden="true">→</span></button></form>
          <div class="security-note"><span class="lock" aria-hidden="true"></span><p>The raw session token is held by the native Rust core and is never returned to this WebView. Signing keys remain separate.</p></div>
        {/if}
      </div>
    </section>
  </main>
{:else}
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand brand--sidebar"><span class="brand-mark" aria-hidden="true">dr</span><span>drugref</span></div>
      <nav aria-label="Primary navigation">
        <p class="nav-label">Workspace</p>
        <button class="nav-item" class:active={activeView === "queue"} type="button" onclick={() => (activeView = "queue")}><span class="nav-icon" aria-hidden="true">⌁</span>Review queue<span class="nav-count">{workspace.summary.interactionRules + workspace.summary.conditionContradictions}</span></button>
        <button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">✓</span>Reviewed entries</button><button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">⌕</span>Evidence library</button>
        <p class="nav-label nav-label--spaced">Administration</p>
        {#if currentUser.role === "administrator"}<button class="nav-item" class:active={activeView === "users"} type="button" onclick={() => (activeView = "users")}><span class="nav-icon" aria-hidden="true">♙</span>Reviewers</button>{/if}
        <button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">◇</span>Signing keys</button>
      </nav>
      <div class="sidebar-status"><div class="status-row"><span class="status-dot status-dot--live"></span><span>Review service connected</span></div><small>Queue read {workspace.generatedAt.slice(0, 10)}</small></div>
    </aside>

    <main class="workspace-shell">
      <header class="topbar"><div><p class="eyebrow eyebrow--ink">{activeView === "users" ? "Administration" : "Clinical curation"}</p><h1>{activeView === "users" ? "Reviewer accounts" : "Review queue"}</h1></div><div class="topbar-actions"><span class="preview-pill"><span></span>{accountMode() === "browser-preview" ? "Browser queue preview" : "Live read-only queue"}</span><div class="reviewer-chip"><span class="avatar">{initials(currentUser.fullName)}</span><span><strong>{currentUser.fullName}</strong><small>{currentUser.qualifications || currentUser.role}</small></span></div><button class="icon-button" type="button" aria-label="Sign out" onclick={signOut}>↗</button></div></header>

      {#if activeView === "users"}
        <UserManagement />
      {:else}
        <section class="metrics" aria-label="Review queue summary"><article><span class="metric-icon metric-icon--amber" aria-hidden="true">↔</span><div><strong>{workspace.summary.interactionRules}</strong><span>Interaction rules</span></div><small>Awaiting judgement</small></article><article><span class="metric-icon metric-icon--rose" aria-hidden="true">±</span><div><strong>{workspace.summary.conditionContradictions}</strong><span>Condition conflicts</span></div><small>Need clinical context</small></article><article><span class="metric-icon metric-icon--green" aria-hidden="true">✓</span><div><strong>{workspace.summary.reviewedPairs}</strong><span>Curated DDI pairs</span></div><small>Expanded from signed rules</small></article></section>
        <section class="review-layout">
          <div class="queue-panel">
            <div class="queue-tools">
              <label class="search-box" aria-label="Search review queue"><span aria-hidden="true">⌕</span><input placeholder="Search drug, class, or relationship" bind:value={search} oninput={scheduleSearch} /></label>
              <div class="filter-row">
                <label><span>Type</span><select bind:value={kindFilter} onchange={applyFilters}><option value="all">All review types</option>{#each workspace.filters.kinds as kind}<option value={kind}>{kindLabel(kind)}</option>{/each}</select></label>
                <label><span>Source</span><select bind:value={sourceFilter} onchange={applyFilters}><option value="all">All sources</option>{#each workspace.filters.sources as source}<option value={source}>{source}</option>{/each}</select></label>
                <label><span>Relationship</span><select bind:value={relationshipFilter} onchange={applyFilters}><option value="all">All relationships</option>{#each workspace.filters.relationships as relationship}<option value={relationship}>{relationship}</option>{/each}</select></label>
              </div>
            </div>
            <div class="queue-heading"><span>{workspace.pagination.totalItems} records · page {workspace.pagination.page} of {Math.max(workspace.pagination.totalPages, 1)}</span><span>{queueLoading ? "Loading…" : "Impact"}</span></div>
            {#if queueError}<p class="queue-error" role="alert">{queueError}</p>{/if}
            <div class="queue-list" aria-busy={queueLoading}>
              {#each workspace.items as item (item.id)}<button class="queue-item" class:selected={selectedItem?.id === item.id} type="button" onclick={() => (selectedId = item.id)}><span class="queue-main"><span class="queue-badges"><span class:conflict={item.kind === "condition_contradiction"}>{kindLabel(item.kind)}</span></span><strong>{item.subjectName}</strong><span class="object-name">{item.objectName}</span><small>{item.relationships.join(" + ")} · {item.candidateSources.join(" + ")}</small></span><span class="impact-count">{item.impactCount}<small>{item.kind === "interaction_rule" ? "pairs" : "conflict"}</small></span></button>{:else}<div class="empty-state">No live records match these filters.</div>{/each}
            </div>
            <div class="queue-pagination"><button type="button" disabled={queueLoading || workspace.pagination.page <= 1} onclick={() => changePage(-1)}>← Previous</button><span>Page {workspace.pagination.page}</span><button type="button" disabled={queueLoading || workspace.pagination.page >= workspace.pagination.totalPages} onclick={() => changePage(1)}>Next →</button></div>
          </div>
          {#if selectedItem}<section class="detail-panel" aria-label={`Review details for ${selectedItem.subjectName}`}><div class="detail-head"><div class="detail-kicker"><span>{kindLabel(selectedItem.kind)}</span><span>•</span><span>{selectedItem.relationships.join(" + ")}</span></div><h2>{selectedItem.subjectName}</h2><p class="relation-line"><span>with</span> {selectedItem.objectName}</p><div class="detail-badges"><span class="badge badge--amber">Unreviewed</span><span class="badge">{selectedItem.candidateSources.join(" + ")}</span><span class="badge">Release {selectedItem.upstreamReleases.join(" / ")}</span></div></div><div class="detail-scroll"><article class="question-card"><p class="section-label">Review question</p><p>{selectedItem.question}</p><div class="impact-callout"><strong>{selectedItem.impactCount}</strong><span>{selectedItem.kind === "interaction_rule" ? "candidate pairs inherit this one rule" : "stable pair carries contradictory projections"}</span></div></article><div class="section-heading"><div><p class="section-label">Clinical judgement</p><h3>Decision fields</h3></div><span>Write path arrives next</span></div><div class="decision-grid" aria-disabled="true"><label><span>Ruling</span><select disabled><option>Choose a ruling…</option></select></label><label><span>Severity</span><select disabled><option>Choose severity…</option></select></label><label class="wide"><span>Mechanism</span><textarea disabled placeholder="Explain the clinical mechanism"></textarea></label><label class="wide"><span>Management</span><textarea disabled placeholder="Describe practical management"></textarea></label><label><span>Evidence grade</span><select disabled><option>Choose evidence…</option></select></label><label><span>Reviewed against</span><input disabled value={selectedItem.upstreamReleases.join(" / ")} /></label></div><div class="section-heading"><div><p class="section-label">Provenance</p><h3>Why this is in the queue</h3></div></div><article class="provenance-card"><div class="provenance-line"><span>Source assertion</span><strong>{selectedItem.candidateSources.join(" + ")}</strong></div><p>{selectedItem.provenance}</p><dl><div><dt>Subject UUID</dt><dd>{selectedItem.subjectUuid}</dd></div><div><dt>Object UUID</dt><dd>{selectedItem.objectUuid}</dd></div></dl></article><div class="section-heading"><div><p class="section-label">Reviewer annotation</p><h3>Working note</h3></div></div><textarea class="annotation" disabled placeholder="Add an evidence-grounded Markdown note…"></textarea></div><footer class="detail-actions"><div><span class="key-indicator"><span></span> Signing remains disabled</span><small>{currentUser.keyCount} enrolled {currentUser.keyCount === 1 ? "key" : "keys"}</small></div><button class="secondary-button" type="button" disabled>Save annotation</button><button class="primary-button primary-button--compact" type="button" disabled>Record &amp; sign decision</button></footer></section>{:else}<div class="detail-panel empty-state">Choose a record to inspect it.</div>{/if}
        </section>
      {/if}
    </main>
  </div>
{/if}
