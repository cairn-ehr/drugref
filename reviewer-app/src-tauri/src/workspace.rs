use serde::{Deserialize, Serialize};
use std::collections::HashSet;

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewerProfile {
    pub username: String,
    pub full_name: String,
    pub qualifications: String,
    pub bio_markdown: String,
    pub key_fingerprint: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct QueueSummary {
    pub interaction_rules: u32,
    pub condition_contradictions: u32,
    pub reviewed_pairs: u32,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewItem {
    pub id: String,
    pub target_key: String,
    pub kind: String,
    pub subject_uuid: String,
    pub object_uuid: String,
    pub subject_name: String,
    pub object_name: String,
    pub relationship: String,
    pub candidate_source: String,
    pub upstream_release: String,
    pub impact_count: u32,
    pub priority: String,
    pub review_state: String,
    pub signature_status: String,
    pub question: String,
    pub provenance: String,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReviewWorkspace {
    pub mode: String,
    pub generated_at: String,
    pub reviewer: ReviewerProfile,
    pub summary: QueueSummary,
    pub items: Vec<ReviewItem>,
}

const DEMO_WORKSPACE: &str = include_str!("../../src/lib/demo-workspace.json");

pub fn load_demo_workspace() -> Result<ReviewWorkspace, String> {
    let workspace: ReviewWorkspace = serde_json::from_str(DEMO_WORKSPACE)
        .map_err(|error| format!("invalid bundled review workspace: {error}"))?;
    validate(&workspace)?;
    Ok(workspace)
}

fn validate(workspace: &ReviewWorkspace) -> Result<(), String> {
    let fingerprint = workspace.reviewer.key_fingerprint.as_bytes();
    if fingerprint.len() != 64
        || !fingerprint
            .iter()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
    {
        return Err("reviewer key fingerprint must be 64 lowercase hex characters".into());
    }

    let mut target_keys = HashSet::new();
    for item in &workspace.items {
        if item.target_key.trim().is_empty()
            || item.subject_uuid.trim().is_empty()
            || item.object_uuid.trim().is_empty()
        {
            return Err(format!("review item {} has an incomplete target", item.id));
        }
        if !target_keys.insert(&item.target_key) {
            return Err(format!("duplicate review target {}", item.target_key));
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::load_demo_workspace;

    #[test]
    fn demo_workspace_has_a_reviewer_and_reviewable_items() {
        let workspace = load_demo_workspace().expect("the bundled fixture should be valid");

        assert_eq!(workspace.reviewer.username, "maya.chen");
        assert!(!workspace.reviewer.full_name.trim().is_empty());
        assert_eq!(workspace.reviewer.key_fingerprint.len(), 64);
        assert_eq!(workspace.summary.interaction_rules, 593);
        assert_eq!(workspace.summary.condition_contradictions, 168);
        assert!(workspace.items.len() >= 5);
    }

    #[test]
    fn demo_queue_uses_unique_stable_targets() {
        let workspace = load_demo_workspace().expect("the bundled fixture should be valid");
        let mut targets = workspace
            .items
            .iter()
            .map(|item| item.target_key.as_str())
            .collect::<Vec<_>>();
        let original_len = targets.len();

        targets.sort_unstable();
        targets.dedup();

        assert_eq!(targets.len(), original_len);
        assert!(workspace
            .items
            .iter()
            .all(|item| !item.subject_uuid.is_empty() && !item.object_uuid.is_empty()));
    }
}
