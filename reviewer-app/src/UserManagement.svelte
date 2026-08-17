<script lang="ts">
  /** Administrator-only reviewer account listing and creation surface. */

  import { onMount } from "svelte";
  import { createUser, listUsers, type ReviewerAccount, type ReviewerRole } from "./lib/accounts";
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

  let users = $state<ReviewerAccount[]>([]);
  let loading = $state(true);
  let submitting = $state(false);
  let error = $state("");
  let success = $state("");
  let username = $state("");
  let fullName = $state("");
  let qualifications = $state("");
  let bioMarkdown = $state("");
  let role = $state<ReviewerRole>("reviewer");
  let password = $state("");
  let passwordConfirmation = $state("");

  onMount(startUserManagement);

  /** Begin loading reviewer accounts without returning a promise to Svelte. */
  function startUserManagement(): void {
    void refresh();
  }

  /** Refresh the current reviewer account projections from the service. */
  async function refresh(): Promise<void> {
    loading = true;
    error = "";
    try {
      users = await listUsers();
    } catch (caught) {
      error = String(caught);
    } finally {
      loading = false;
    }
  }

  /** Validate and create a reviewer account from the administrator form. */
  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = "";
    success = "";
    if (password !== passwordConfirmation) {
      error = "Passwords do not match.";
      return;
    }
    submitting = true;
    try {
      const user = await createUser({
        username,
        fullName,
        qualifications,
        bioMarkdown,
        role,
        password,
      });
      users = [...users, user].sort((left, right) => left.username.localeCompare(right.username));
      success = `${user.fullName} can now sign in.`;
      username = "";
      fullName = "";
      qualifications = "";
      bioMarkdown = "";
      role = "reviewer";
      password = "";
      passwordConfirmation = "";
    } catch (caught) {
      error = String(caught);
    } finally {
      submitting = false;
    }
  }
</script>

<section class="user-layout">
  <article class="admin-card user-list-card">
    <div class="admin-card-head">
      <div><p class="section-label">Registered accounts</p><h2>Reviewers</h2></div>
      <span class="count-pill">{users.length}</span>
    </div>
    {#if loading}
      <p class="admin-empty">Loading reviewer accounts…</p>
    {:else if users.length === 0}
      <p class="admin-empty">No reviewer accounts are registered.</p>
    {:else}
      <div class="user-list">
        {#each users as user (user.reviewerUuid)}
          <div class="user-row">
            <span class="avatar">{reviewerInitials(user.fullName)}</span>
            <span class="user-identity"><strong>{user.fullName}</strong><small>@{user.username} · {user.qualifications || "No qualifications recorded"}</small></span>
            <span class="role-pill" class:role-pill--admin={user.role === "administrator"}>{user.role === "administrator" ? "Administrator" : "Reviewer"}</span>
            <span class="key-count">{keyCountLabel(user.keyCount)}</span>
          </div>
        {/each}
      </div>
    {/if}
  </article>

  <article class="admin-card create-user-card">
    <div class="admin-card-head"><div><p class="section-label">Account administration</p><h2>Create a user</h2></div></div>
    <p class="admin-intro">Create the stable username, initial profile, role, and Argon2id-backed credential in one transaction.</p>
    <form class="admin-form" onsubmit={submit}>
      <label><span>Username</span><input autocomplete="off" required minlength={USERNAME_MIN_LENGTH} maxlength={USERNAME_MAX_LENGTH} pattern={USERNAME_PATTERN} bind:value={username} /></label>
      <label><span>Full name</span><input autocomplete="off" required maxlength={FULL_NAME_MAX_LENGTH} bind:value={fullName} /></label>
      <div class="admin-form-row">
        <label><span>Qualifications</span><input autocomplete="off" maxlength={QUALIFICATIONS_MAX_LENGTH} bind:value={qualifications} /></label>
        <label><span>Role</span><select bind:value={role}><option value="reviewer">Reviewer</option><option value="administrator">Administrator</option></select></label>
      </div>
      <label><span>Brief biography <small>Markdown source</small></span><textarea maxlength={BIOGRAPHY_MAX_LENGTH} bind:value={bioMarkdown}></textarea></label>
      <div class="admin-form-row">
        <label><span>Initial password</span><input type="password" autocomplete="new-password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} bind:value={password} /></label>
        <label><span>Confirm password</span><input type="password" autocomplete="new-password" required minlength={PASSWORD_MIN_LENGTH} maxlength={PASSWORD_MAX_LENGTH} bind:value={passwordConfirmation} /></label>
      </div>
      {#if error}<p class="form-error" role="alert">{error}</p>{/if}
      {#if success}<p class="form-success" role="status">{success}</p>{/if}
      <button class="primary-button" type="submit" disabled={submitting}>{submitting ? "Creating user…" : "Create user"}<span aria-hidden="true">→</span></button>
    </form>
  </article>
</section>
