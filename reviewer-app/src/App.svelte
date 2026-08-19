<script lang="ts">
  /** Top-level reviewer lifecycle, navigation, and live queue orchestration. */

  import { onMount } from "svelte";
  import UserManagement from "./UserManagement.svelte";
  import KeyTrust from "./KeyTrust.svelte";
  import SigningKeys from "./SigningKeys.svelte";
  import PendingSignatures from "./PendingSignatures.svelte";
  import WorkingRecords from "./WorkingRecords.svelte";
  import ClinicalDecision from "./ClinicalDecision.svelte";
  import {
    accountMode,
    bootstrapAdmin,
    login as authenticate,
    logout,
    startupState,
    type CreateAccountInput,
    type ReviewerAccount,
  } from "./lib/accounts";
  import {
    BIOGRAPHY_MAX_LENGTH,
    FIRST_QUEUE_PAGE,
    FULL_NAME_MAX_LENGTH,
    NEXT_PAGE_DELTA,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PREVIOUS_PAGE_DELTA,
    QUALIFICATIONS_MAX_LENGTH,
    SEARCH_DEBOUNCE_MILLISECONDS,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    USERNAME_PATTERN,
  } from "./lib/constants";
  import {
    queueReadDate,
    retainedQueueSelection,
    reviewerInitials,
    reviewKindLabel,
    unresolvedQueueCount,
    visiblePageCount,
  } from "./lib/presentation";
  import { ALL_FILTERS, buildReviewQueueQuery } from "./lib/queue";
  import type { ReviewKind, ReviewQueueItem, ReviewQueuePage } from "./lib/types";
  import { loadReviewQueue } from "./lib/workspace";

  /** Top-level application surfaces with distinct data-loading requirements. */
  type Screen = "checking" | "bootstrap" | "login" | "workspace" | "unavailable";

  /** Authenticated workspace sections selectable from the sidebar. */
  type View = "queue" | "pending" | "users" | "signing" | "trust";

  /** Initial sequence number used to reject stale overlapping queue responses. */
  const INITIAL_QUEUE_REQUEST = 0;

  /** Amount by which each new queue request advances the request sequence. */
  const QUEUE_REQUEST_INCREMENT = 1;

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
  let kindFilter = $state<typeof ALL_FILTERS | ReviewKind>(ALL_FILTERS);
  let sourceFilter = $state(ALL_FILTERS);
  let relationshipFilter = $state(ALL_FILTERS);
  let queueLoading = $state(false);
  let queueError = $state("");
  let queueRequest = INITIAL_QUEUE_REQUEST;
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
  let recordedTargets = $state<string[]>([]);

  let selectedItem = $derived(workspace ? retainedQueueSelection(workspace.items, selectedId) : null);

  onMount(startApplication);

  /** Begin the asynchronous startup check without returning a promise to Svelte. */
  function startApplication(): void {
    void checkStartup();
  }

  /** Determine whether startup should display bootstrap or ordinary sign-in. */
  async function checkStartup(): Promise<void> {
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

  /** Retry either startup discovery or workspace loading after a service failure. */
  async function retryUnavailable(): Promise<void> {
    if (!currentUser) {
      await checkStartup();
      return;
    }
    authError = "";
    loading = true;
    try {
      await refreshQueue(FIRST_QUEUE_PAGE);
      activeView = "queue";
      screen = "workspace";
    } catch (error) {
      authError = `The review workspace still could not load: ${String(error)}`;
    } finally {
      loading = false;
    }
  }

  /** Authenticate submitted credentials and open the live review workspace. */
  async function submitLogin(event: SubmitEvent): Promise<void> {
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

  /** Create the first administrator after confirming the two password entries. */
  async function submitBootstrap(event: SubmitEvent): Promise<void> {
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

  /** Retain the authenticated reviewer and load the first live queue page. */
  async function openWorkspace(user: ReviewerAccount): Promise<void> {
    currentUser = user;
    try {
      await refreshQueue(FIRST_QUEUE_PAGE);
      activeView = "queue";
      screen = "workspace";
    } catch (error) {
      screen = "unavailable";
      throw new Error(`Account access succeeded, but the review workspace could not load: ${String(error)}`);
    }
  }

  /** Load one filtered queue page while preventing stale responses from winning. */
  async function refreshQueue(page: number = FIRST_QUEUE_PAGE): Promise<void> {
    const request = ++queueRequest;
    queueLoading = true;
    queueError = "";
    try {
      const loaded = await loadReviewQueue(
        buildReviewQueueQuery(page, {
          search,
          kind: kindFilter,
          source: sourceFilter,
          relationship: relationshipFilter,
        }),
      );
      if (request !== queueRequest) return;
      workspace = loaded;
      const retained = retainedQueueSelection(loaded.items, selectedId);
      selectedId = retained?.id ?? "";
    } catch (error) {
      if (request !== queueRequest) return;
      queueError = String(error);
      if (!workspace) throw error;
    } finally {
      if (request === queueRequest) queueLoading = false;
    }
  }

  /** Reset paging and apply the currently selected exact-match filters. */
  function applyFilters(): void {
    void refreshQueue(FIRST_QUEUE_PAGE);
  }

  /** Debounce literal queue search so typing does not issue a request per keystroke. */
  function scheduleSearch(): void {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(
      (): void => void refreshQueue(FIRST_QUEUE_PAGE),
      SEARCH_DEBOUNCE_MILLISECONDS,
    );
  }

  /** Request a page relative to the currently loaded queue page. */
  function changePage(delta: number): void {
    if (workspace) void refreshQueue(workspace.pagination.page + delta);
  }

  /** Revoke the current session and clear all authenticated workspace state. */
  async function signOut(): Promise<void> {
    try {
      await logout();
    } finally {
      clearWorkspaceSession();
    }
  }

  /** Clear WebView workspace state after native or service session invalidation. */
  function clearWorkspaceSession(): void {
    workspace = null;
    currentUser = null;
    selectedId = "";
    queueRequest += QUEUE_REQUEST_INCREMENT;
    if (searchTimer) clearTimeout(searchTimer);
    password = accountMode() === "browser-preview" ? "preview" : "";
    screen = "login";
  }

  /** Select the review queue workspace view. */
  function showQueue(): void {
    activeView = "queue";
  }

  /** Select current curated revisions whose detached signing can be resumed. */
  function showPendingSignatures(): void {
    activeView = "pending";
  }

  /** Select the reviewer-account administration view. */
  function showUsers(): void {
    activeView = "users";
  }

  /** Select the current reviewer's local signing-key surface. */
  function showSigningKeys(): void {
    activeView = "signing";
  }

  /** Select administrator-owned public signing-key trust management. */
  function showKeyTrust(): void {
    activeView = "trust";
  }

  /** Keep the signed-in reviewer chip aligned with refreshed enrolment status. */
  function updateKeyCount(keyCount: number): void {
    if (currentUser) currentUser = { ...currentUser, keyCount };
  }

  /** Keep the application shell aligned with a self-profile correction. */
  function updateCurrentUser(reviewer: ReviewerAccount): void {
    currentUser = reviewer;
    if (reviewer.role !== "administrator") activeView = "queue";
  }

  /** Select a queue item for inspection and append-only working history. */
  function selectQueueItem(item: ReviewQueueItem): void {
    selectedId = item.id;
  }

  /** Mark a recorded target locally and refresh the live queue that should retire it. */
  function decisionRecorded(targetKey: string): void {
    if (!recordedTargets.includes(targetKey)) recordedTargets = [...recordedTargets, targetKey];
    void refreshQueue(FIRST_QUEUE_PAGE);
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
          <p class="eyebrow eyebrow--ink">Service unavailable</p><h2>{currentUser ? "Review workspace could not be loaded" : "Reviewer access could not be checked"}</h2><p class="login-intro">No workspace data has been loaded. Start or restart the reviewer service and confirm the current migrations are applied.</p>
          <p class="form-error" role="alert">{authError}</p><button class="primary-button" type="button" disabled={loading} onclick={retryUnavailable}>{loading ? "Trying again…" : "Try again"}<span aria-hidden="true">→</span></button>
        {:else if screen === "bootstrap"}
          <p class="eyebrow eyebrow--ink">First-run registration</p><h2 id="bootstrap-title">Create the first administrator</h2><p class="login-intro">No administrator is registered. This account must be created before Drugref Reviewer can finish starting.</p>
          <form class="bootstrap-form" onsubmit={submitBootstrap} aria-labelledby="bootstrap-title">
            <div class="auth-form-row"><label><span>Username</span><input required minlength={USERNAME_MIN_LENGTH} maxlength={USERNAME_MAX_LENGTH} pattern={USERNAME_PATTERN} autocomplete="username" bind:value={bootstrapInput.username} /></label><label><span>Full name</span><input required maxlength={FULL_NAME_MAX_LENGTH} autocomplete="name" bind:value={bootstrapInput.fullName} /></label></div>
            <div class="auth-form-row"><label><span>Qualifications</span><input maxlength={QUALIFICATIONS_MAX_LENGTH} bind:value={bootstrapInput.qualifications} /></label><label><span>Role</span><input value="Administrator" disabled /></label></div>
            <label><span>Brief biography <small>Markdown source</small></span><textarea maxlength={BIOGRAPHY_MAX_LENGTH} bind:value={bootstrapInput.bioMarkdown}></textarea></label>
            <div class="auth-form-row"><label><span>Password</span><input type="password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} autocomplete="new-password" bind:value={bootstrapInput.password} /></label><label><span>Confirm password</span><input type="password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} autocomplete="new-password" bind:value={bootstrapConfirmation} /></label></div>
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
        <button class="nav-item" class:active={activeView === "queue"} type="button" onclick={showQueue}><span class="nav-icon" aria-hidden="true">⌁</span>Review queue<span class="nav-count">{unresolvedQueueCount(workspace.summary)}</span></button>
        <button class="nav-item" class:active={activeView === "pending"} type="button" onclick={showPendingSignatures}><span class="nav-icon" aria-hidden="true">✓</span>Pending signatures</button><button class="nav-item" type="button" disabled><span class="nav-icon" aria-hidden="true">⌕</span>Evidence library</button>
        <p class="nav-label nav-label--spaced">Administration</p>
        {#if currentUser.role === "administrator"}<button class="nav-item" class:active={activeView === "users"} type="button" onclick={showUsers}><span class="nav-icon" aria-hidden="true">♙</span>Reviewers</button><button class="nav-item" class:active={activeView === "trust"} type="button" onclick={showKeyTrust}><span class="nav-icon" aria-hidden="true">◈</span>Key trust</button>{/if}
        <button class="nav-item" class:active={activeView === "signing"} type="button" onclick={showSigningKeys}><span class="nav-icon" aria-hidden="true">◇</span>Signing keys</button>
      </nav>
      <div class="sidebar-status"><div class="status-row"><span class="status-dot status-dot--live"></span><span>Review service connected</span></div><small>Queue read {queueReadDate(workspace.generatedAt)}</small></div>
    </aside>

    <main class="workspace-shell">
      <header class="topbar"><div><p class="eyebrow eyebrow--ink">{activeView === "queue" || activeView === "pending" ? "Clinical curation" : "Administration"}</p><h1>{activeView === "users" ? "Reviewer accounts" : activeView === "signing" ? "Signing keys" : activeView === "trust" ? "Key trust" : activeView === "pending" ? "Pending signatures" : "Review queue"}</h1></div><div class="topbar-actions"><span class="preview-pill"><span></span>{accountMode() === "browser-preview" ? "Browser queue preview" : "Live review service"}</span><div class="reviewer-chip"><span class="avatar">{reviewerInitials(currentUser.fullName)}</span><span><strong>{currentUser.fullName}</strong><small>{currentUser.qualifications || currentUser.role}</small></span></div><button class="icon-button" type="button" aria-label="Sign out" onclick={signOut}>↗</button></div></header>

      {#if activeView === "users"}
        <UserManagement {currentUser} onCurrentUserUpdated={updateCurrentUser} onCurrentSessionRevoked={clearWorkspaceSession} />
      {:else if activeView === "trust"}
        <KeyTrust />
      {:else if activeView === "signing"}
        <SigningKeys onEnrolled={updateKeyCount} />
      {:else if activeView === "pending"}
        <PendingSignatures />
      {:else}
        <section class="metrics" aria-label="Review queue summary"><article><span class="metric-icon metric-icon--amber" aria-hidden="true">↔</span><div><strong>{workspace.summary.interactionRules}</strong><span>Interaction rules</span></div><small>Awaiting judgement</small></article><article><span class="metric-icon metric-icon--rose" aria-hidden="true">±</span><div><strong>{workspace.summary.conditionContradictions}</strong><span>Condition conflicts</span></div><small>Need clinical context</small></article><article><span class="metric-icon metric-icon--green" aria-hidden="true">✓</span><div><strong>{workspace.summary.reviewedPairs}</strong><span>Curated DDI pairs</span></div><small>Expanded from live rules</small></article></section>
        <section class="review-layout">
          <div class="queue-panel">
            <div class="queue-tools">
              <label class="search-box" aria-label="Search review queue"><span aria-hidden="true">⌕</span><input placeholder="Search drug, class, or relationship" bind:value={search} oninput={scheduleSearch} /></label>
              <div class="filter-row">
                <label><span>Type</span><select bind:value={kindFilter} onchange={applyFilters}><option value={ALL_FILTERS}>All review types</option>{#each workspace.filters.kinds as kind}<option value={kind}>{reviewKindLabel(kind)}</option>{/each}</select></label>
                <label><span>Source</span><select bind:value={sourceFilter} onchange={applyFilters}><option value={ALL_FILTERS}>All sources</option>{#each workspace.filters.sources as source}<option value={source}>{source}</option>{/each}</select></label>
                <label><span>Relationship</span><select bind:value={relationshipFilter} onchange={applyFilters}><option value={ALL_FILTERS}>All relationships</option>{#each workspace.filters.relationships as relationship}<option value={relationship}>{relationship}</option>{/each}</select></label>
              </div>
            </div>
            <div class="queue-heading"><span>{workspace.pagination.totalItems} records · page {workspace.pagination.page} of {visiblePageCount(workspace.pagination.totalPages)}</span><span>{queueLoading ? "Loading…" : "Impact"}</span></div>
            {#if queueError}<p class="queue-error" role="alert">{queueError}</p>{/if}
            <div class="queue-list" aria-busy={queueLoading}>
              {#each workspace.items as item (item.id)}<button class="queue-item" class:selected={selectedItem?.id === item.id} type="button" onclick={() => selectQueueItem(item)}><span class="queue-main"><span class="queue-badges"><span class:conflict={item.kind === "condition_contradiction"}>{reviewKindLabel(item.kind)}</span></span><strong>{item.subjectName}</strong><span class="object-name">{item.objectName}</span><small>{item.relationships.join(" + ")} · {item.candidateSources.join(" + ")}</small></span><span class="impact-count">{item.impactCount}<small>{item.kind === "interaction_rule" ? "pairs" : "conflict"}</small></span></button>{:else}<div class="empty-state">No live records match these filters.</div>{/each}
            </div>
            <div class="queue-pagination"><button type="button" disabled={queueLoading || workspace.pagination.page <= FIRST_QUEUE_PAGE} onclick={() => changePage(PREVIOUS_PAGE_DELTA)}>← Previous</button><span>Page {workspace.pagination.page}</span><button type="button" disabled={queueLoading || workspace.pagination.page >= workspace.pagination.totalPages} onclick={() => changePage(NEXT_PAGE_DELTA)}>Next →</button></div>
          </div>
          {#if selectedItem}
            <section class="detail-panel" aria-label={`Review details for ${selectedItem.subjectName}`}>
              <div class="detail-head">
                <div class="detail-kicker"><span>{reviewKindLabel(selectedItem.kind)}</span><span>•</span><span>{selectedItem.relationships.join(" + ")}</span></div>
                <h2>{selectedItem.subjectName}</h2>
                <p class="relation-line"><span>with</span> {selectedItem.objectName}</p>
                <div class="detail-badges"><span class="badge badge--amber">{recordedTargets.includes(selectedItem.targetKey) ? "Recorded · unsigned" : "Unreviewed"}</span><span class="badge">{selectedItem.candidateSources.join(" + ")}</span><span class="badge">Release {selectedItem.upstreamReleases.join(" / ")}</span></div>
              </div>
              <div class="detail-scroll">
                <article class="question-card"><p class="section-label">Review question</p><p>{selectedItem.question}</p><div class="impact-callout"><strong>{selectedItem.impactCount}</strong><span>{selectedItem.kind === "interaction_rule" ? "candidate pairs inherit this one rule" : "stable pair carries contradictory projections"}</span></div></article>
                {#key `decision-${selectedItem.id}`}<ClinicalDecision item={selectedItem} onRecorded={decisionRecorded} />{/key}
                <div class="section-heading"><div><p class="section-label">Provenance</p><h3>Why this is in the queue</h3></div></div>
                <article class="provenance-card"><div class="provenance-line"><span>Source assertion</span><strong>{selectedItem.candidateSources.join(" + ")}</strong></div><p>{selectedItem.provenance}</p><dl><div><dt>Subject UUID</dt><dd>{selectedItem.subjectUuid}</dd></div><div><dt>Object UUID</dt><dd>{selectedItem.objectUuid}</dd></div></dl></article>

                {#key selectedItem.id}<WorkingRecords item={selectedItem} />{/key}
              </div>
            </section>
          {:else}<div class="detail-panel empty-state">Choose a record to inspect it.</div>{/if}
        </section>
      {/if}
    </main>
  </div>
{/if}
