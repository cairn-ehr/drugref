//! Departed graded pairs in the GUI (#283): list every reviewed grade that stopped
//! reaching drugs it once graded, and acknowledge one drug's departure.
//!
//! WHY THIS MODULE HOLDS NO RULES. What departed, which drug left, and what an
//! acknowledgement closes are db/087's SQL functions, shared with the Python CLI
//! (`drugref audit departures`, `drugref curate acknowledge-departure`), so the GUI
//! and the CLI cannot disagree. The acknowledgement takes db/086's ledger lock with
//! a bounded wait (#289) inside SQL; this module runs it in its own transaction,
//! which rolls back on every error path because an uncommitted sqlx `Transaction`
//! rolls back when dropped.

use axum::{extract::State, http::HeaderMap, http::StatusCode, Json};
use chrono::{DateTime, Utc};
use reviewer_domain::{
    closes_per_leaver, AcknowledgeDepartureRequest, DepartureAcknowledgement, DepartureLeaver,
    GradedPairDeparture, RuleGrain,
};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use crate::{authenticate_headers, decisions::parse_severity, AppError, AppState};

/// Every departure with names for display, most recent ruling first (the CLI's
/// order). Leavers come as two arrays aggregated in one pass with one ORDER BY, so
/// uuid and name stay aligned.
const LIST_SQL: &str = "\
SELECT d.rule_grain, d.ruling_id, d.subject_key,
       coalesce(sm.display_name, sc.class_name) AS subject_name,
       d.object_class_uuid, oc.class_name AS object_class_name, d.relationship,
       d.severity, d.reviewed_by, d.reviewed_against, d.backfilled, d.first_seen_at,
       d.subject_moieties, d.partner_moieties,
       coalesce(lv.uuids, '{}') AS leaver_uuids, coalesce(lv.names, '{}') AS leaver_names
FROM   drugref.departed_graded_pairs() d
LEFT   JOIN drugref.substance_moiety sm
       ON d.rule_grain = 'moiety_rule' AND sm.moiety_uuid = d.subject_key
LEFT   JOIN drugref.substance_class sc
       ON d.rule_grain = 'class_rule' AND sc.class_uuid = d.subject_key
JOIN   drugref.substance_class oc ON oc.class_uuid = d.object_class_uuid
CROSS  JOIN LATERAL (
       SELECT array_agg(l.moiety ORDER BY m.display_name, l.moiety) AS uuids,
              array_agg(m.display_name ORDER BY m.display_name, l.moiety) AS names
       FROM   drugref.departure_leavers(d.rule_grain, d.ruling_id, d.subject_moieties,
                                        d.partner_moieties) AS l(moiety)
       JOIN   drugref.substance_moiety m ON m.moiety_uuid = l.moiety) lv
ORDER  BY d.reviewed_at DESC, d.rule_grain, d.ruling_id DESC";

/// One row of `LIST_SQL`.
#[derive(FromRow)]
struct DepartureRow {
    rule_grain: String,
    ruling_id: i64,
    subject_key: Uuid,
    subject_name: Option<String>,
    object_class_uuid: Uuid,
    object_class_name: String,
    relationship: String,
    severity: String,
    reviewed_by: String,
    reviewed_against: String,
    backfilled: bool,
    first_seen_at: DateTime<Utc>,
    subject_moieties: Vec<Uuid>,
    partner_moieties: Vec<Uuid>,
    leaver_uuids: Vec<Uuid>,
    leaver_names: Vec<String>,
}

impl TryFrom<DepartureRow> for GradedPairDeparture {
    type Error = AppError;

    /// Parse database vocabularies and count what each leaver's acknowledgement closes.
    fn try_from(row: DepartureRow) -> Result<Self, Self::Error> {
        // The arrays hold one entry per open episode, and a pair can have two; count
        // distinct pairs, as the acknowledgement's result and the CLI do.
        let mut pairs: Vec<(Uuid, Uuid)> = row
            .subject_moieties
            .into_iter()
            .zip(row.partner_moieties)
            .collect();
        pairs.sort_unstable();
        pairs.dedup();
        let leavers = closes_per_leaver(&pairs, &row.leaver_uuids)
            .into_iter()
            .zip(row.leaver_names)
            .map(|((moiety_uuid, closes), display_name)| DepartureLeaver {
                moiety_uuid,
                display_name,
                closes,
            })
            .collect();
        Ok(Self {
            rule_grain: RuleGrain::parse(&row.rule_grain)
                .ok_or_else(|| AppError::internal(format!("unknown grain {}", row.rule_grain)))?,
            ruling_id: row.ruling_id,
            subject_key: row.subject_key,
            subject_name: row
                .subject_name
                .unwrap_or_else(|| row.subject_key.to_string()),
            object_class_uuid: row.object_class_uuid,
            object_class_name: row.object_class_name,
            relationship: row.relationship,
            severity: parse_severity(&row.severity)?,
            reviewed_by: row.reviewed_by,
            reviewed_against: row.reviewed_against,
            backfilled: row.backfilled,
            first_seen_at: row.first_seen_at.to_rfc3339(),
            pair_count: u32::try_from(pairs.len()).unwrap_or(u32::MAX),
            leavers,
        })
    }
}

/// Read every open departure (the unfiltered list `drugref audit departures` shows).
pub async fn list(pool: &PgPool) -> Result<Vec<GradedPairDeparture>, AppError> {
    sqlx::query_as::<_, DepartureRow>(LIST_SQL)
        .fetch_all(pool)
        .await?
        .into_iter()
        .map(GradedPairDeparture::try_from)
        .collect()
}

/// Acknowledge every open departed pair of the ruling's key that names the drug,
/// recorded as `reviewed_by` against the derived release (db/087), and commit.
pub async fn acknowledge(
    pool: &PgPool,
    input: &AcknowledgeDepartureRequest,
    reviewed_by: &str,
) -> Result<DepartureAcknowledgement, AppError> {
    let mut transaction = pool.begin().await?;
    let rows: Vec<(Uuid, Uuid, String)> = sqlx::query_as(
        "SELECT acked_subject, acked_partner, acked_against \
         FROM drugref.acknowledge_departure($1, $2, $3, $4, $5, NULL)",
    )
    .bind(input.rule_grain.as_str())
    .bind(input.ruling_id)
    .bind(input.moiety_uuid)
    .bind(&input.rationale)
    .bind(reviewed_by)
    .fetch_all(&mut *transaction)
    .await
    .map_err(|error| {
        let sqlstate = error.as_database_error().and_then(|d| d.code());
        match refusal(sqlstate.as_deref()) {
            Some(refused) => {
                // The detail (LG003 names the lock holder) is for operators only.
                tracing::warn!(?sqlstate, %error, "departure acknowledgement refused");
                refused
            }
            None => error.into(),
        }
    })?;
    // LG002 guarantees at least one row; an empty result is a broken invariant.
    let Some((_, _, reviewed_against)) = rows.first().cloned() else {
        return Err(AppError::internal(
            "acknowledge_departure returned no rows without raising LG002",
        ));
    };
    transaction.commit().await?;
    Ok(DepartureAcknowledgement {
        closed_pairs: u32::try_from(rows.len()).unwrap_or(u32::MAX),
        reviewed_against,
    })
}

/// The client-safe response for each of db/087's refusals; `None` for any other
/// SQLSTATE. The detail (LG003 names database backends and roles) goes to the
/// service log, never to a reviewer.
fn refusal(sqlstate: Option<&str>) -> Option<AppError> {
    match sqlstate? {
        "LG001" => Some(AppError::not_found("no ruling has that id")),
        "LG002" => Some(AppError::conflict(
            "nothing of this drug's departure is still open; another reviewer may have \
             acknowledged it. Reload the list",
        )),
        "LG003" => Some(AppError::unavailable(
            "the departure ledger is busy (another session holds its lock); try again \
             shortly, and ask an operator if it persists",
        )),
        "LG004" => Some(AppError::conflict(
            "no finished ingest release to record this acknowledgement against",
        )),
        _ => None,
    }
}

/// List every open departure for any authenticated reviewer.
pub(crate) async fn list_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<GradedPairDeparture>>, AppError> {
    authenticate_headers(&state, &headers).await?;
    Ok(Json(list(&state.pool).await?))
}

/// Acknowledge one drug's departure as the authenticated reviewer.
pub(crate) async fn acknowledge_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(input): Json<AcknowledgeDepartureRequest>,
) -> Result<(StatusCode, Json<DepartureAcknowledgement>), AppError> {
    let authenticated = authenticate_headers(&state, &headers).await?;
    let input = input
        .validate()
        .map_err(|error| AppError::bad_request(error.0))?;
    let done = acknowledge(&state.pool, &input, &authenticated.reviewer.full_name).await?;
    Ok((StatusCode::CREATED, Json(done)))
}

#[cfg(test)]
mod tests {
    use axum::http::StatusCode;
    use reviewer_domain::{AcknowledgeDepartureRequest, RuleGrain};

    use chrono::Utc;
    use reviewer_domain::GradedPairDeparture;
    use uuid::Uuid;

    use super::{acknowledge, list, refusal, DepartureRow};
    use crate::live_graded_seed::{live_pool, seed_gui_moiety_ruling};

    /// Two open episodes of one pair (`test_two_open_episodes_for_one_pair_are_both_
    /// acknowledged`) come back from SQL as two array entries; the GUI must count the
    /// pair once, as the acknowledgement's DISTINCT result and the CLI's frozenset do.
    #[test]
    fn duplicate_open_episodes_count_as_one_pair() {
        let (x, p, q) = (Uuid::from_u128(1), Uuid::from_u128(2), Uuid::from_u128(3));
        let row = DepartureRow {
            rule_grain: "moiety_rule".into(),
            ruling_id: 7,
            subject_key: x,
            subject_name: Some("X".into()),
            object_class_uuid: Uuid::from_u128(9),
            object_class_name: "C".into(),
            relationship: "interacts".into(),
            severity: "major".into(),
            reviewed_by: "R".into(),
            reviewed_against: "MED-RT 1".into(),
            backfilled: false,
            first_seen_at: Utc::now(),
            subject_moieties: vec![x, x, x],
            partner_moieties: vec![p, p, q],
            leaver_uuids: vec![p],
            leaver_names: vec!["P".into()],
        };
        let departure = GradedPairDeparture::try_from(row).expect("parses");
        assert_eq!(departure.pair_count, 2);
        assert_eq!(departure.leavers[0].closes, 1);
    }

    #[test]
    fn each_ledger_refusal_has_its_own_status_and_others_pass_through() {
        let status = |code| refusal(Some(code)).map(|error| error.status());
        assert_eq!(status("LG001"), Some(StatusCode::NOT_FOUND));
        assert_eq!(status("LG002"), Some(StatusCode::CONFLICT));
        assert_eq!(status("LG003"), Some(StatusCode::SERVICE_UNAVAILABLE));
        assert_eq!(status("LG004"), Some(StatusCode::CONFLICT));
        assert!(refusal(Some("23514")).is_none());
        assert!(refusal(None).is_none());
    }

    /// The GUI path end to end (#283): a GUI ruling's partner leaves its class, the
    /// list shows the departure with that partner as the one leaver closing one pair,
    /// the acknowledgement records the reviewer and the membership-derived release
    /// (the stayer's run), the departure is gone, and acknowledging again is refused.
    #[tokio::test]
    #[ignore = "requires a migrated Drugref PostgreSQL database"]
    async fn live_departure_is_listed_and_acknowledged() {
        let pool = live_pool().await;
        let seeded = seed_gui_moiety_ruling(&pool).await;
        sqlx::query("DELETE FROM drugref.class_membership WHERE moiety_uuid = $1")
            .bind(seeded.partner)
            .execute(&pool)
            .await
            .expect("partner leaves");

        let ours = |all: Vec<reviewer_domain::GradedPairDeparture>| {
            all.into_iter()
                .filter(|d| d.subject_key == seeded.subject)
                .collect::<Vec<_>>()
        };
        let listed = ours(list(&pool).await.expect("departures"));
        assert_eq!(listed.len(), 1);
        let departure = &listed[0];
        assert_eq!(departure.rule_grain, RuleGrain::MoietyRule);
        assert_eq!(departure.object_class_uuid, seeded.object_class);
        assert_eq!(departure.subject_name, "Subject");
        assert_eq!(departure.pair_count, 1);
        assert_eq!(departure.leavers.len(), 1);
        assert_eq!(departure.leavers[0].moiety_uuid, seeded.partner);
        assert_eq!(departure.leavers[0].display_name, "Partner");
        assert_eq!(departure.leavers[0].closes, 1);
        // the stayer is still graded, so it is not a leaver
        assert_ne!(departure.leavers[0].moiety_uuid, seeded.stayer);

        let request = AcknowledgeDepartureRequest {
            rule_grain: departure.rule_grain,
            ruling_id: departure.ruling_id,
            moiety_uuid: seeded.partner,
            rationale: "Partner reclassified upstream".into(),
        };
        let done = acknowledge(&pool, &request, "Test Reviewer")
            .await
            .expect("acknowledgement");
        assert_eq!(done.closed_pairs, 1);
        assert_eq!(done.reviewed_against, format!("MED-RT {}", seeded.release));
        let recorded: (String, String) = sqlx::query_as(
            "SELECT a.reviewed_by, a.rationale FROM drugref.graded_pair_departure_ack a \
             JOIN drugref.graded_pair_observation o USING (observation_id) \
             WHERE o.subject_key = $1",
        )
        .bind(seeded.subject)
        .fetch_one(&pool)
        .await
        .expect("recorded acknowledgement");
        assert_eq!(
            recorded,
            (
                "Test Reviewer".into(),
                "Partner reclassified upstream".into()
            )
        );
        assert!(ours(list(&pool).await.expect("departures")).is_empty());

        let again = acknowledge(&pool, &request, "Test Reviewer")
            .await
            .expect_err("nothing left to acknowledge");
        assert_eq!(again.status(), StatusCode::CONFLICT);
    }
}
