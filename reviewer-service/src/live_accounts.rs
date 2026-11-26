//! Shared setup for the live (`#[ignore]`) tests that create administrators (#282).
//!
//! `administration`'s round trip needs a database with no administrator, because it
//! bootstraps one and then checks that the last one cannot be disabled. `signing`'s
//! round trip inserts an administrator of its own. Run together, the signing
//! test's administrator broke the administration test whenever it appeared (before
//! the count check or during the last-admin check), and every run left one behind.
//! Both now hold one session advisory lock for their whole run. Both name their
//! administrators with `TEST_ADMIN_PREFIX`, so the administration test can retire
//! the ones earlier runs left.

use sqlx::{Connection, PgConnection, PgPool};

/// Username prefix of every administrator a live test creates.
pub const TEST_ADMIN_PREFIX: &str = "live-admin-test-";

/// Wait for, then hold, the account tests' lock. It is a SESSION lock on a
/// connection of its own (not a pooled one, which would keep the lock after
/// returning to the pool). Dropping the connection at the end of the test, or
/// when it panics, ends the session and releases the lock.
pub async fn serialise(database_url: &str) -> PgConnection {
    let mut connection = PgConnection::connect(database_url)
        .await
        .expect("account-test lock connection");
    sqlx::query("SELECT pg_advisory_lock(hashtext('drugref reviewer-service live account tests'))")
        .execute(&mut connection)
        .await
        .expect("account-test lock");
    connection
}

/// Retire every live administrator that an earlier live test created, so
/// `bootstrap_admin` sees none again. Deactivating them is not enough:
/// `bootstrap_admin` counts inactive administrators too, and the API's last-admin
/// guard refuses to demote the last active one. So this demotes each one to an
/// inactive reviewer with the same append-then-supersede protocol as
/// `update_user_profile`, in one statement. Unlike that function it records the
/// retired reviewer as its own actor and revokes no sessions (test accounts hold
/// none that matter). History stays intact, and administrators created outside
/// these tests are never touched.
pub async fn retire_earlier_test_administrators(pool: &PgPool) {
    sqlx::query(
        "WITH old AS ( \
             SELECT p.* FROM drugref.reviewer_profile p \
             JOIN drugref.reviewer_account a USING (reviewer_uuid) \
             WHERE a.username LIKE $1 || '%' AND p.role = 'administrator' \
               AND p.superseded_by IS NULL), \
         new AS ( \
             INSERT INTO drugref.reviewer_profile (reviewer_uuid, full_name, \
                 qualifications, bio_markdown, role, active, recorded_by) \
             SELECT reviewer_uuid, full_name, qualifications, bio_markdown, \
                    'reviewer', false, reviewer_uuid FROM old \
             RETURNING reviewer_profile_id, reviewer_uuid) \
         UPDATE drugref.reviewer_profile p SET superseded_by = new.reviewer_profile_id \
         FROM old JOIN new USING (reviewer_uuid) \
         WHERE p.reviewer_profile_id = old.reviewer_profile_id",
    )
    .bind(TEST_ADMIN_PREFIX)
    .execute(pool)
    .await
    .expect("retire earlier test administrators");
}
