//! Read-only projection of unresolved clinical gaps into paginated review targets.

use chrono::{DateTime, Utc};
use reviewer_domain::{
    Pagination, ReviewKind, ReviewQueueFilters, ReviewQueueItem, ReviewQueuePage,
    ReviewQueueSummary, ValidatedReviewQueueQuery,
};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use crate::AppError;

const EMPTY_ITEM_COUNT: i64 = 0;
const PARTIAL_PAGE_INCREMENT: i64 = 1;

/// Materialised queue projection shared by totals, filters, and the requested page.
///
/// Both queue views are candidate projections. This query enriches them with the
/// source/release rows that produced each candidate, then materialises the union once
/// so the page, totals and available filters describe one database snapshot.
const REVIEW_QUEUE_SQL: &str = r#"
WITH interaction_items AS (
    SELECT 'interaction_rule'::text AS kind,
           rr.subject_moiety_uuid AS subject_uuid,
           rr.object_class_uuid AS object_uuid,
           sm.display_name AS subject_name,
           sc.class_name AS object_name,
           ARRAY[rr.relationship]::text[] AS relationships,
           array_agg(DISTINCT rr.source ORDER BY rr.source) AS candidate_sources,
           array_agg(DISTINCT r.upstream_release ORDER BY r.upstream_release) AS upstream_releases,
           max(CASE
                 WHEN rr.expands_descendants
                  AND coalesce(policy.decision, 'allow') <> 'deny'
                 THEN rr.subtree_partner_count
                 ELSE rr.direct_partner_count
               END)::bigint AS impact_count
    -- ci_rule_partner_reach is the one database definition of a rule's direct and
    -- subtree reach. Reading those counts avoids expanding every candidate pair just
    -- to count it again for the queue; the authoritative gap view uses the same
    -- policy choice by counting the pair-level ddi_candidate_pair projection.
    FROM drugref.ci_rule_partner_reach rr
    JOIN drugref.substance_moiety sm
      ON sm.moiety_uuid = rr.subject_moiety_uuid
    JOIN drugref.substance_class sc
      ON sc.class_uuid = rr.object_class_uuid
    JOIN drugref.ingest_run r ON r.ingest_run_id = rr.ingest_run
    LEFT JOIN drugref.class_expansion_policy_current policy
      ON policy.source = sc.source
     AND policy.source_code = sc.source_code
    WHERE NOT EXISTS (
        SELECT 1
        FROM drugref.curated_interaction curated
        WHERE curated.subject_moiety_uuid = rr.subject_moiety_uuid
          AND curated.object_class_uuid = rr.object_class_uuid
          AND curated.relationship = rr.relationship
          AND curated.superseded_by IS NULL
    )
    GROUP BY rr.subject_moiety_uuid, rr.object_class_uuid, sm.display_name,
             sc.class_name, rr.relationship
    -- A rule reaching no partner belongs to the separate population/dead-policy
    -- queues, exactly as gap_uncurated_interaction_rule specifies.
    HAVING max(CASE
                 WHEN rr.expands_descendants
                  AND coalesce(policy.decision, 'allow') <> 'deny'
                 THEN rr.subtree_partner_count
                 ELSE rr.direct_partner_count
               END) > 0
),
condition_provenance AS (
    SELECT subject_moiety_uuid AS subject_uuid,
           object_condition_uuid AS object_uuid,
           relationship, source, ingest_run
    FROM drugref.moiety_condition_contraindication
    UNION ALL
    SELECT subject_moiety_uuid, object_condition_uuid, relationship, source, ingest_run
    FROM drugref.moiety_condition_indication
),
condition_items AS (
    SELECT 'condition_contradiction'::text AS kind,
           g.subject_moiety AS subject_uuid,
           g.object_condition AS object_uuid,
           g.display_name AS subject_name,
           g.condition_name AS object_name,
           array_agg(DISTINCT p.relationship ORDER BY p.relationship) AS relationships,
           array_agg(DISTINCT p.source ORDER BY p.source) AS candidate_sources,
           array_agg(DISTINCT r.upstream_release ORDER BY r.upstream_release) AS upstream_releases,
           1::bigint AS impact_count -- one stable drug-condition pair
    FROM drugref.gap_uncurated_condition_contradiction g
    JOIN condition_provenance p
      ON p.subject_uuid = g.subject_moiety
     AND p.object_uuid = g.object_condition
    JOIN drugref.ingest_run r ON r.ingest_run_id = p.ingest_run
    GROUP BY g.subject_moiety, g.object_condition, g.display_name, g.condition_name
),
base AS MATERIALIZED (
    SELECT * FROM interaction_items
    UNION ALL
    SELECT * FROM condition_items
),
filtered AS (
    SELECT *
    FROM base
    WHERE ($1::text IS NULL OR kind = $1)
      AND ($2::text IS NULL OR $2 = ANY(candidate_sources))
      AND ($3::text IS NULL OR $3 = ANY(relationships))
      AND ($4::text IS NULL OR strpos(
            lower(subject_name || ' ' || object_name || ' ' || array_to_string(relationships, ' ')),
            lower($4)
          ) > 0)
),
metadata AS (
    SELECT transaction_timestamp() AS generated_at,
           count(*) FILTER (WHERE kind = 'interaction_rule')::bigint AS interaction_rules,
           count(*) FILTER (WHERE kind = 'condition_contradiction')::bigint AS condition_contradictions,
           (SELECT count(*)::bigint FROM drugref.curated_ddi_pair) AS reviewed_pairs,
           coalesce(array_agg(DISTINCT kind ORDER BY kind), ARRAY[]::text[]) AS kinds,
           coalesce((
               SELECT array_agg(DISTINCT source ORDER BY source)
               FROM base b CROSS JOIN LATERAL unnest(b.candidate_sources) AS source
           ), ARRAY[]::text[]) AS sources,
           coalesce((
               SELECT array_agg(DISTINCT relationship ORDER BY relationship)
               FROM base b CROSS JOIN LATERAL unnest(b.relationships) AS relationship
           ), ARRAY[]::text[]) AS filter_relationships,
           (SELECT count(*)::bigint FROM filtered) AS total_items
    FROM base
),
page_rows AS (
    SELECT *
    FROM filtered
    ORDER BY impact_count DESC, lower(subject_name), lower(object_name),
             subject_uuid, object_uuid, relationships
    LIMIT $5 OFFSET $6
)
SELECT m.generated_at, m.interaction_rules, m.condition_contradictions,
       m.reviewed_pairs, m.kinds, m.sources, m.filter_relationships, m.total_items,
       p.kind, p.subject_uuid, p.object_uuid, p.subject_name, p.object_name,
       p.relationships, p.candidate_sources, p.upstream_releases, p.impact_count
FROM metadata m
LEFT JOIN page_rows p ON true
ORDER BY p.impact_count DESC NULLS LAST, lower(p.subject_name), lower(p.object_name),
         p.subject_uuid, p.object_uuid, p.relationships
"#;

/// One denormalised SQL result row containing snapshot metadata and an optional item.
#[derive(Clone, FromRow)]
struct QueueRow {
    generated_at: DateTime<Utc>,
    interaction_rules: i64,
    condition_contradictions: i64,
    reviewed_pairs: i64,
    kinds: Vec<String>,
    sources: Vec<String>,
    filter_relationships: Vec<String>,
    total_items: i64,
    kind: Option<String>,
    subject_uuid: Option<Uuid>,
    object_uuid: Option<Uuid>,
    subject_name: Option<String>,
    object_name: Option<String>,
    relationships: Option<Vec<String>>,
    candidate_sources: Option<Vec<String>>,
    upstream_releases: Option<Vec<String>>,
    impact_count: Option<i64>,
}

/// Execute the queue projection and build its typed API response.
pub async fn load(
    pool: &PgPool,
    query: &ValidatedReviewQueueQuery,
) -> Result<ReviewQueuePage, AppError> {
    let rows = sqlx::query_as::<_, QueueRow>(REVIEW_QUEUE_SQL)
        .bind(query.kind.map(ReviewKind::as_str))
        .bind(query.source.as_deref())
        .bind(query.relationship.as_deref())
        .bind(query.search.as_deref())
        .bind(i64::from(query.page_size))
        .bind(query.offset())
        .fetch_all(pool)
        .await?;
    build_page(rows, query)
}

/// Convert denormalised SQL rows into one queue page and its snapshot metadata.
fn build_page(
    rows: Vec<QueueRow>,
    query: &ValidatedReviewQueueQuery,
) -> Result<ReviewQueuePage, AppError> {
    let first = rows
        .first()
        .cloned()
        .ok_or_else(|| AppError::internal("review queue query returned no metadata row"))?;
    let total_pages = page_count(first.total_items, query.page_size);
    let filters = ReviewQueueFilters {
        kinds: first
            .kinds
            .iter()
            .map(|value| parse_kind(value))
            .collect::<Result<_, _>>()?,
        sources: first.sources.clone(),
        relationships: first.filter_relationships.clone(),
    };
    let items = rows
        .into_iter()
        .filter(|row| row.kind.is_some())
        .map(build_item)
        .collect::<Result<_, _>>()?;

    Ok(ReviewQueuePage {
        generated_at: first.generated_at.to_rfc3339(),
        summary: ReviewQueueSummary {
            interaction_rules: first.interaction_rules,
            condition_contradictions: first.condition_contradictions,
            reviewed_pairs: first.reviewed_pairs,
        },
        filters,
        pagination: Pagination {
            page: query.page,
            page_size: query.page_size,
            total_items: first.total_items,
            total_pages,
        },
        items,
    })
}

/// Return the number of pages needed for a non-negative filtered record count.
fn page_count(total_items: i64, page_size: u16) -> u32 {
    let page_size = i64::from(page_size);
    let complete_pages = total_items / page_size;
    let partial_page = if total_items % page_size == EMPTY_ITEM_COUNT {
        EMPTY_ITEM_COUNT
    } else {
        PARTIAL_PAGE_INCREMENT
    };
    (complete_pages + partial_page) as u32
}

/// Convert one complete queue row into a stable, human-readable review target.
fn build_item(row: QueueRow) -> Result<ReviewQueueItem, AppError> {
    let kind = parse_kind(required(row.kind, "kind")?.as_str())?;
    let subject_uuid = required(row.subject_uuid, "subject UUID")?;
    let object_uuid = required(row.object_uuid, "object UUID")?;
    let subject_name = required(row.subject_name, "subject name")?;
    let object_name = required(row.object_name, "object name")?;
    let relationships = required(row.relationships, "relationships")?;
    let candidate_sources = required(row.candidate_sources, "candidate sources")?;
    let upstream_releases = required(row.upstream_releases, "upstream releases")?;
    let impact_count = required(row.impact_count, "impact count")?;

    let (id, target_key, question, provenance) = match kind {
        ReviewKind::InteractionRule => {
            let relationship = relationships.first().ok_or_else(|| {
                AppError::internal("interaction queue row has no relationship")
            })?;
            (
                format!("interaction-{subject_uuid}-{object_uuid}-{relationship}"),
                format!(
                    "MOIETY:{subject_uuid}/CLASS:{object_uuid}/CI_AXIS:{relationship}"
                ),
                format!(
                    "Does {subject_name} have a clinically actionable interaction with members of {object_name}?"
                ),
                format!(
                    "A source rule expanded through the Drugref class graph to {impact_count} candidate drug pairs."
                ),
            )
        }
        ReviewKind::ConditionContradiction => (
            format!("condition-{subject_uuid}-{object_uuid}"),
            format!("MOIETY:{subject_uuid}/CONDITION:{object_uuid}"),
            format!(
                "How should Drugref rule when {subject_name} is projected as both indicated and contraindicated for {object_name}?"
            ),
            "The same stable drug-condition pair is reached by indication and contraindication projections."
                .into(),
        ),
    };

    Ok(ReviewQueueItem {
        id,
        target_key,
        kind,
        subject_uuid,
        object_uuid,
        subject_name,
        object_name,
        relationships,
        candidate_sources,
        upstream_releases,
        impact_count,
        question,
        provenance,
    })
}

/// Parse the closed database vocabulary for review target kinds.
fn parse_kind(value: &str) -> Result<ReviewKind, AppError> {
    match value {
        "interaction_rule" => Ok(ReviewKind::InteractionRule),
        "condition_contradiction" => Ok(ReviewKind::ConditionContradiction),
        value => Err(AppError::internal(format!(
            "unknown review queue kind {value}"
        ))),
    }
}

/// Require one field that is optional only because metadata rows use a left join.
fn required<T>(value: Option<T>, label: &str) -> Result<T, AppError> {
    value.ok_or_else(|| AppError::internal(format!("review queue row has no {label}")))
}

#[cfg(test)]
mod tests {
    use super::{load, page_count, parse_kind};
    use reviewer_domain::{ReviewKind, ReviewQueueQuery};
    use sqlx::postgres::PgPoolOptions;

    const DEFAULT_PAGE_SIZE: usize = 25;
    const FILTERED_PAGE_SIZE: u16 = 5;

    /// Pin every review-kind spelling emitted by the database query.
    #[test]
    fn database_kind_values_are_exhaustive() {
        assert_eq!(
            parse_kind("interaction_rule").expect("interaction kind"),
            ReviewKind::InteractionRule
        );
        assert_eq!(
            parse_kind("condition_contradiction").expect("condition kind"),
            ReviewKind::ConditionContradiction
        );
        assert!(parse_kind("future_kind").is_err());
    }

    /// Verify page-count arithmetic at empty, exact, and partial page boundaries.
    #[test]
    fn page_count_rounds_up_only_for_partial_pages() {
        assert_eq!(page_count(0, FILTERED_PAGE_SIZE), 0);
        assert_eq!(
            page_count(i64::from(FILTERED_PAGE_SIZE), FILTERED_PAGE_SIZE),
            1
        );
        assert_eq!(
            page_count(i64::from(FILTERED_PAGE_SIZE) + 1, FILTERED_PAGE_SIZE),
            2
        );
    }

    /// Exercise paging, filters, and metadata against an explicitly supplied live database.
    #[tokio::test]
    #[ignore = "requires a populated Drugref PostgreSQL database"]
    async fn live_queue_query_reads_pages_filters_and_metadata() {
        let database_url = std::env::var("DRUGREF_REVIEW_TEST_DATABASE_URL")
            .expect("DRUGREF_REVIEW_TEST_DATABASE_URL must name a populated database");
        let pool = PgPoolOptions::new()
            .max_connections(1)
            .connect(&database_url)
            .await
            .expect("review queue test database");
        let query = ReviewQueueQuery::default()
            .validate()
            .expect("default queue query");
        let page = load(&pool, &query).await.expect("live review queue");

        // The queue deliberately uses ci_rule_partner_reach instead of enumerating
        // ddi_candidate_pair. Keep its row population pinned to the authoritative
        // gap views so a future policy/count rewrite cannot make the fast projection
        // quietly disagree with the registry that mints open questions.
        let authoritative_interactions: i64 = sqlx::query_scalar(
            "SELECT count(*)::bigint FROM drugref.gap_uncurated_interaction_rule",
        )
        .fetch_one(&pool)
        .await
        .expect("authoritative interaction gap count");
        let authoritative_conditions: i64 = sqlx::query_scalar(
            "SELECT count(*)::bigint FROM drugref.gap_uncurated_condition_contradiction",
        )
        .fetch_one(&pool)
        .await
        .expect("authoritative condition gap count");

        assert!(page.summary.interaction_rules > 0);
        assert!(page.summary.condition_contradictions > 0);
        assert_eq!(page.summary.interaction_rules, authoritative_interactions);
        assert_eq!(
            page.summary.condition_contradictions,
            authoritative_conditions
        );
        assert_eq!(page.items.len(), DEFAULT_PAGE_SIZE);
        assert!(page.filters.sources.iter().any(|source| source == "MED-RT"));
        assert!(page.items.iter().all(|item| !item.target_key.is_empty()));

        let conditions = ReviewQueueQuery {
            page_size: Some(FILTERED_PAGE_SIZE),
            kind: Some(ReviewKind::ConditionContradiction),
            ..Default::default()
        }
        .validate()
        .expect("condition filter");
        let page = load(&pool, &conditions)
            .await
            .expect("filtered condition queue");
        assert_eq!(page.items.len(), usize::from(FILTERED_PAGE_SIZE));
        assert_eq!(
            page.pagination.total_items,
            page.summary.condition_contradictions
        );
        assert!(page
            .items
            .iter()
            .all(|item| item.kind == ReviewKind::ConditionContradiction));
    }
}
