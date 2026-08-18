<script lang="ts">
  /** Administrator-only reviewer lifecycle and append-only account corrections. */

  import { onMount } from "svelte";
  import {
    createUser,
    listUsers,
    revokeUserSessions,
    rotateUserPassword,
    updateUserProfile,
    type ReviewerAccount,
    type ReviewerRole,
  } from "./lib/accounts";
  import {
    BIOGRAPHY_MAX_LENGTH,
    FULL_NAME_MAX_LENGTH,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    QUALIFICATIONS_MAX_LENGTH,
    USERNAME_MAX_LENGTH,
    USERNAME_MIN_LENGTH,
    USERNAME_PATTERN,
  } from "./lib/constants";
  import { keyCountLabel, reviewerInitials } from "./lib/presentation";

  let {
    currentUser,
    onCurrentUserUpdated,
    onCurrentSessionRevoked,
  }: {
    currentUser: ReviewerAccount;
    onCurrentUserUpdated: (reviewer: ReviewerAccount) => void;
    onCurrentSessionRevoked: () => void;
  } = $props();

  let users = $state<ReviewerAccount[]>([]);
  let selectedId = $state("");
  let creating = $state(false);
  let loading = $state(true);
  let submitting = $state(false);
  let error = $state("");
  let success = $state("");

  let username = $state("");
  let fullName = $state("");
  let qualifications = $state("");
  let bioMarkdown = $state("");
  let role = $state<ReviewerRole>("reviewer");
  let active = $state(true);
  let password = $state("");
  let passwordConfirmation = $state("");
  let profilePreviewing = $state(false);
  let passwordConfirming = $state(false);
  let sessionConfirming = $state(false);

  let selectedUser = $derived(users.find((user) => user.reviewerUuid === selectedId) ?? null);

  onMount((): void => void refresh());

  /** Refresh current projections and retain the selected stable identity. */
  async function refresh(): Promise<void> {
    loading = true;
    error = "";
    try {
      users = await listUsers();
      const retained = users.find((user) => user.reviewerUuid === selectedId);
      selectUser(retained ?? users.find((user) => user.reviewerUuid === currentUser.reviewerUuid) ?? users[0]);
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
    }
  }

  /** Open one current profile in the correction form. */
  function selectUser(user: ReviewerAccount | undefined): void {
    if (!user) return;
    selectedId = user.reviewerUuid;
    creating = false;
    fullName = user.fullName;
    qualifications = user.qualifications;
    bioMarkdown = user.bioMarkdown;
    role = user.role;
    active = user.active;
    clearActionState();
  }

  /** Open a blank stable-account creation form. */
  function startCreate(): void {
    creating = true;
    selectedId = "";
    username = "";
    fullName = "";
    qualifications = "";
    bioMarkdown = "";
    role = "reviewer";
    active = true;
    password = "";
    passwordConfirmation = "";
    clearActionState();
  }

  /** Reset transient confirmations and status messages when context changes. */
  function clearActionState(): void {
    error = "";
    success = "";
    profilePreviewing = false;
    passwordConfirming = false;
    sessionConfirming = false;
    password = "";
    passwordConfirmation = "";
  }

  /** Validate and create a stable reviewer identity and its initial facts. */
  async function submitCreate(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = "";
    success = "";
    if (password !== passwordConfirmation) {
      error = "Passwords do not match.";
      return;
    }
    submitting = true;
    try {
      const user = await createUser({ username, fullName, qualifications, bioMarkdown, role, password });
      users = sortedUsers([...users, user]);
      selectUser(user);
      success = `${user.fullName} can now sign in.`;
    } catch (caught) {
      error = String(caught);
    } finally {
      submitting = false;
    }
  }

  /** Show the exact complete replacement before appending a profile correction. */
  function previewProfile(event: SubmitEvent): void {
    event.preventDefault();
    error = "";
    success = "";
    profilePreviewing = true;
  }

  /** Append the confirmed profile replacement and adopt its database projection. */
  async function saveProfile(): Promise<void> {
    if (!selectedUser) return;
    submitting = true;
    error = "";
    success = "";
    try {
      const result = await updateUserProfile(selectedUser.reviewerUuid, {
        fullName,
        qualifications,
        bioMarkdown,
        role,
        active,
        expectedProfileRevisionId: selectedUser.profileRevisionId,
      });
      replaceUser(result.reviewer);
      profilePreviewing = false;
      success = result.reviewer.active
        ? `Profile revision ${result.reviewer.profileRevisionId} is current.`
        : `Access disabled; ${sessionCountLabel(result.revokedSessionCount)} revoked.`;
      if (result.reviewer.reviewerUuid === currentUser.reviewerUuid) {
        onCurrentUserUpdated(result.reviewer);
        if (!result.reviewer.active) onCurrentSessionRevoked();
      }
    } catch (caught) {
      error = String(caught);
    } finally {
      submitting = false;
    }
  }

  /** Validate the replacement password before showing its revocation consequence. */
  function previewPassword(event: SubmitEvent): void {
    event.preventDefault();
    error = "";
    success = "";
    if (password !== passwordConfirmation) {
      error = "Passwords do not match.";
      return;
    }
    passwordConfirming = true;
  }

  /** Rotate the credential and invalidate every session authenticated by its predecessor. */
  async function savePassword(): Promise<void> {
    if (!selectedUser) return;
    submitting = true;
    error = "";
    success = "";
    try {
      const result = await rotateUserPassword(selectedUser.reviewerUuid, password);
      replaceUser(result.reviewer);
      password = "";
      passwordConfirmation = "";
      passwordConfirming = false;
      success = `Password rotated; ${sessionCountLabel(result.revokedSessionCount)} revoked.`;
      if (result.reviewer.reviewerUuid === currentUser.reviewerUuid) onCurrentSessionRevoked();
    } catch (caught) {
      error = String(caught);
      password = "";
      passwordConfirmation = "";
      passwordConfirming = false;
    } finally {
      submitting = false;
    }
  }

  /** Append administrative revocations for every live session after confirmation. */
  async function revokeSessions(): Promise<void> {
    if (!selectedUser) return;
    submitting = true;
    error = "";
    success = "";
    try {
      const result = await revokeUserSessions(selectedUser.reviewerUuid);
      replaceUser(result.reviewer);
      sessionConfirming = false;
      success = `${sessionCountLabel(result.revokedSessionCount)} revoked.`;
      if (result.reviewer.reviewerUuid === currentUser.reviewerUuid) onCurrentSessionRevoked();
    } catch (caught) {
      error = String(caught);
    } finally {
      submitting = false;
    }
  }

  /** Replace one current projection after a database-derived mutation response. */
  function replaceUser(reviewer: ReviewerAccount): void {
    users = sortedUsers(users.map((user) => user.reviewerUuid === reviewer.reviewerUuid ? reviewer : user));
    selectedId = reviewer.reviewerUuid;
  }

  /** Keep the stable username order used by the service. */
  function sortedUsers(input: ReviewerAccount[]): ReviewerAccount[] {
    return input.sort((left, right) => left.username.localeCompare(right.username));
  }

  /** Describe a count of affected sessions without implying secrets are exposed. */
  function sessionCountLabel(count: number): string {
    return `${count} ${count === 1 ? "session" : "sessions"}`;
  }
</script>

<section class="user-layout">
  <article class="admin-card user-list-card">
    <div class="admin-card-head">
      <div><p class="section-label">Registered accounts</p><h2>Reviewers</h2></div>
      <div class="admin-card-actions"><span class="count-pill">{users.length}</span><button class="secondary-button" type="button" disabled={loading} onclick={() => void refresh()}>Refresh</button><button class="secondary-button" type="button" onclick={startCreate}>Add reviewer</button></div>
    </div>
    {#if loading}
      <p class="admin-empty">Loading reviewer accounts…</p>
    {:else if users.length === 0}
      <p class="admin-empty">No reviewer accounts are registered.</p>
    {:else}
      <div class="user-list">
        {#each users as user (user.reviewerUuid)}
          <button class="user-row" class:user-row--selected={user.reviewerUuid === selectedId && !creating} type="button" onclick={() => selectUser(user)}>
            <span class="avatar">{reviewerInitials(user.fullName)}</span>
            <span class="user-identity"><strong>{user.fullName}</strong><small>@{user.username} · {user.qualifications || "No qualifications recorded"}</small></span>
            <span class="account-state" class:account-state--disabled={!user.active}>{user.active ? "Enabled" : "Disabled"}</span>
            <span class="user-facts"><span class="role-pill" class:role-pill--admin={user.role === "administrator"}>{user.role === "administrator" ? "Administrator" : "Reviewer"}</span><small>{user.liveSessionCount} live · {keyCountLabel(user.keyCount)}</small></span>
          </button>
        {/each}
      </div>
    {/if}
  </article>

  <article class="admin-card account-editor-card">
    {#if creating}
      <div class="admin-card-head"><div><p class="section-label">Stable identity</p><h2>Create a reviewer</h2></div></div>
      <p class="admin-intro">Create the immutable username, initial profile, role, and Argon2id-backed credential in one transaction.</p>
      <form class="admin-form" onsubmit={submitCreate}>
        <label><span>Username</span><input autocomplete="off" required minlength={USERNAME_MIN_LENGTH} maxlength={USERNAME_MAX_LENGTH} pattern={USERNAME_PATTERN} bind:value={username} /></label>
        <label><span>Full name</span><input autocomplete="off" required maxlength={FULL_NAME_MAX_LENGTH} bind:value={fullName} /></label>
        <div class="admin-form-row"><label><span>Qualifications</span><input autocomplete="off" maxlength={QUALIFICATIONS_MAX_LENGTH} bind:value={qualifications} /></label><label><span>Role</span><select bind:value={role}><option value="reviewer">Reviewer</option><option value="administrator">Administrator</option></select></label></div>
        <label><span>Brief biography <small>Markdown source</small></span><textarea maxlength={BIOGRAPHY_MAX_LENGTH} bind:value={bioMarkdown}></textarea></label>
        <div class="admin-form-row"><label><span>Initial password</span><input type="password" autocomplete="new-password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} bind:value={password} /></label><label><span>Confirm password</span><input type="password" autocomplete="new-password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} bind:value={passwordConfirmation} /></label></div>
        {#if error}<p class="form-error" role="alert">{error}</p>{/if}
        <button class="primary-button" type="submit" disabled={submitting}>{submitting ? "Creating reviewer…" : "Create reviewer"}<span aria-hidden="true">→</span></button>
      </form>
    {:else if selectedUser}
      <div class="admin-card-head">
        <div><p class="section-label">Append-only administration</p><h2>{selectedUser.fullName}</h2><small class="immutable-username">@{selectedUser.username} · profile revision {selectedUser.profileRevisionId}</small></div>
        <span class="account-state" class:account-state--disabled={!selectedUser.active}>{selectedUser.active ? "Enabled" : "Disabled"}</span>
      </div>
      <div class="account-editor-scroll">
        <form class="admin-form profile-form" onsubmit={previewProfile}>
          <div class="admin-form-row"><label><span>Full name</span><input required maxlength={FULL_NAME_MAX_LENGTH} bind:value={fullName} /></label><label><span>Qualifications</span><input maxlength={QUALIFICATIONS_MAX_LENGTH} bind:value={qualifications} /></label></div>
          <label><span>Brief biography <small>Markdown source</small></span><textarea maxlength={BIOGRAPHY_MAX_LENGTH} bind:value={bioMarkdown}></textarea></label>
          <div class="admin-form-row"><label><span>Role</span><select bind:value={role}><option value="reviewer">Reviewer</option><option value="administrator">Administrator</option></select></label><label><span>Access</span><select bind:value={active}><option value={true}>Enabled</option><option value={false}>Disabled</option></select></label></div>
          <button class="secondary-button admin-action-button" type="submit" disabled={submitting}>Review profile correction</button>
          {#if profilePreviewing}
            <div class="account-confirmation" class:account-confirmation--danger={selectedUser.active && !active}>
              <p><strong>Record complete profile revision?</strong> This supersedes revision {selectedUser.profileRevisionId} without deleting it.{#if selectedUser.active && !active}{" "}Disabling also revokes {sessionCountLabel(selectedUser.liveSessionCount)}.{/if}</p>
              <div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={submitting} onclick={() => profilePreviewing = false}>Keep editing</button><button class="primary-button primary-button--compact" class:selected-danger={selectedUser.active && !active} type="button" disabled={submitting} onclick={() => void saveProfile()}>{submitting ? "Recording…" : "Record correction"}</button></div>
            </div>
          {/if}
        </form>

        <div class="account-security">
          <section>
            <p class="section-label">Credential rotation</p><h3>Set a replacement password</h3><p>Rotation appends a new Argon2id credential and revokes every current session.</p>
            <form class="security-form" onsubmit={previewPassword}><div class="admin-form-row"><label><span>New password</span><input type="password" autocomplete="new-password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} bind:value={password} /></label><label><span>Confirm password</span><input type="password" autocomplete="new-password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} bind:value={passwordConfirmation} /></label></div><button class="secondary-button" type="submit" disabled={submitting}>Rotate password</button></form>
            {#if passwordConfirming}<div class="account-confirmation account-confirmation--danger"><p><strong>Rotate credential and revoke {sessionCountLabel(selectedUser.liveSessionCount)}?</strong> Existing sessions cannot be restored.</p><div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={submitting} onclick={() => passwordConfirming = false}>Cancel</button><button class="danger-button" type="button" disabled={submitting} onclick={() => void savePassword()}>{submitting ? "Rotating…" : "Rotate and revoke"}</button></div></div>{/if}
          </section>
          <section>
            <p class="section-label">Session control</p><h3>{sessionCountLabel(selectedUser.liveSessionCount)} live</h3><p>Append an administrative revocation for every unexpired session without changing the profile or password.</p>
            {#if sessionConfirming}<div class="account-confirmation account-confirmation--danger"><p><strong>Revoke every live session?</strong> The reviewer must sign in again.</p><div class="decision-preview-actions"><button class="secondary-button" type="button" disabled={submitting} onclick={() => sessionConfirming = false}>Cancel</button><button class="danger-button" type="button" disabled={submitting || selectedUser.liveSessionCount === 0} onclick={() => void revokeSessions()}>{submitting ? "Revoking…" : "Revoke sessions"}</button></div></div>{:else}<button class="secondary-button" type="button" disabled={submitting || selectedUser.liveSessionCount === 0} onclick={() => sessionConfirming = true}>Revoke all sessions</button>{/if}
          </section>
        </div>
        {#if error}<p class="form-error account-message" role="alert">{error}</p>{/if}
        {#if success}<p class="form-success account-message" role="status">{success}</p>{/if}
      </div>
    {:else}
      <p class="admin-empty">Choose a reviewer to administer.</p>
    {/if}
  </article>
</section>
