//! Shared contracts for departed graded pairs: a reviewed grade that stopped
//! reaching drugs it once graded, and its acknowledgement from the GUI (#283).
//!
//! The rules for what departed, who left and what an acknowledgement closes live
//! in SQL (db/087), shared with the Python CLI; this crate only carries their
//! results and validates what a reviewer types.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{validate_text, Severity, ValidationError};

const REQUIRED_TEXT_MIN_LENGTH: usize = 1;
const RATIONALE_MAX_LENGTH: usize = 10_000;

/// Grain of a ruling, in the ledger's own spelling (`graded_pair_observation`).
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuleGrain {
    /// A drug-to-class ruling (`curated_interaction`).
    MoietyRule,
    /// A class-to-class ruling (`curated_class_interaction`).
    ClassRule,
}

impl RuleGrain {
    /// Return the stable database and wire spelling of the grain.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::MoietyRule => "moiety_rule",
            Self::ClassRule => "class_rule",
        }
    }

    /// Parse the database spelling; `None` for anything else.
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "moiety_rule" => Some(Self::MoietyRule),
            "class_rule" => Some(Self::ClassRule),
            _ => None,
        }
    }
}

/// One drug that left a departed ruling, and how many pairs acknowledging it closes.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DepartureLeaver {
    /// The drug that left.
    pub moiety_uuid: Uuid,
    /// The drug's display name.
    pub display_name: String,
    /// Departed pairs of this ruling that name the drug on either side.
    pub closes: u32,
}

/// One live ruling whose reviewed grade no longer reaches pairs it once graded.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GradedPairDeparture {
    /// Grain of the ruling.
    pub rule_grain: RuleGrain,
    /// The ruling's LIVE version id (acknowledge with this).
    pub ruling_id: i64,
    /// Subject moiety (moiety grain) or subject class (class grain).
    pub subject_key: Uuid,
    /// Display name of the subject moiety or class.
    pub subject_name: String,
    /// The class the ruling is against.
    pub object_class_uuid: Uuid,
    /// Name of that class.
    pub object_class_name: String,
    /// Contraindication axis of the ruling.
    pub relationship: String,
    /// The live version's severity.
    pub severity: Severity,
    /// Who reviewed the live version.
    pub reviewed_by: String,
    /// What the live version was reviewed against.
    pub reviewed_against: String,
    /// True when an open episode was reconstructed by db/083's migration.
    pub backfilled: bool,
    /// When the earliest open episode was first seen (RFC 3339).
    pub first_seen_at: String,
    /// Number of departed (subject, partner) pairs.
    pub pair_count: u32,
    /// The drugs that left, each with what acknowledging it closes.
    pub leavers: Vec<DepartureLeaver>,
}

/// Authenticated request to acknowledge one drug's departure from one ruling.
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AcknowledgeDepartureRequest {
    /// Grain of the ruling.
    pub rule_grain: RuleGrain,
    /// Any version's id of the ruling.
    pub ruling_id: i64,
    /// The drug whose departed pairs are acknowledged.
    pub moiety_uuid: Uuid,
    /// Why the departure is correct.
    pub rationale: String,
}

impl AcknowledgeDepartureRequest {
    /// Require a positive ruling id and a trimmed rationale before storage is accessed.
    pub fn validate(mut self) -> Result<Self, ValidationError> {
        if self.ruling_id <= 0 {
            return Err(ValidationError("rulingId must be positive".into()));
        }
        validate_text(
            "rationale",
            &self.rationale,
            REQUIRED_TEXT_MIN_LENGTH,
            RATIONALE_MAX_LENGTH,
        )?;
        self.rationale = self.rationale.trim().to_string();
        Ok(self)
    }
}

/// What one acknowledgement closed.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DepartureAcknowledgement {
    /// Distinct (subject, partner) pairs closed.
    pub closed_pairs: u32,
    /// The release the acknowledgement was recorded against (derived, db/087).
    pub reviewed_against: String,
}

/// For each leaver, in the given order, how many `pairs` name it on either side.
/// Given distinct pairs, that is exactly what acknowledging that drug closes,
/// because the list a reviewer sees is the unfiltered departure (db/087 closes
/// every pair of the key with the drug).
pub fn closes_per_leaver(pairs: &[(Uuid, Uuid)], leavers: &[Uuid]) -> Vec<(Uuid, u32)> {
    leavers
        .iter()
        .map(|drug| {
            let closes = pairs
                .iter()
                .filter(|(subject, partner)| subject == drug || partner == drug)
                .count();
            (*drug, u32::try_from(closes).unwrap_or(u32::MAX))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use uuid::Uuid;

    use super::{closes_per_leaver, AcknowledgeDepartureRequest, RuleGrain};

    fn request(rationale: &str) -> AcknowledgeDepartureRequest {
        AcknowledgeDepartureRequest {
            rule_grain: RuleGrain::ClassRule,
            ruling_id: 7,
            moiety_uuid: Uuid::nil(),
            rationale: rationale.to_string(),
        }
    }

    #[test]
    fn a_blank_rationale_is_refused() {
        assert!(request("   ").validate().is_err());
    }

    #[test]
    fn a_non_positive_ruling_id_is_refused() {
        for ruling_id in [0, -1] {
            let input = AcknowledgeDepartureRequest {
                ruling_id,
                ..request("left on purpose")
            };
            assert!(input.validate().is_err());
        }
    }

    #[test]
    fn an_unknown_grain_is_refused_on_the_wire() {
        let body = r#"{"ruleGrain":"class","rulingId":7,
            "moietyUuid":"00000000-0000-0000-0000-000000000000","rationale":"x"}"#;
        assert!(serde_json::from_str::<AcknowledgeDepartureRequest>(body).is_err());
    }

    #[test]
    fn a_rationale_is_trimmed() {
        assert_eq!(
            request("  left on purpose \n")
                .validate()
                .unwrap()
                .rationale,
            "left on purpose"
        );
    }

    #[test]
    fn grains_use_the_ledger_spelling() {
        assert_eq!(RuleGrain::MoietyRule.as_str(), "moiety_rule");
        assert_eq!(RuleGrain::parse("class_rule"), Some(RuleGrain::ClassRule));
        assert_eq!(RuleGrain::parse("class"), None);
        assert_eq!(
            serde_json::to_string(&RuleGrain::ClassRule).unwrap(),
            "\"class_rule\""
        );
    }

    #[test]
    fn each_leaver_closes_the_pairs_it_is_in_on_either_side() {
        let (x, y, p) = (Uuid::from_u128(1), Uuid::from_u128(2), Uuid::from_u128(3));
        // a class against itself: x appears as subject and as partner
        let pairs = [(x, p), (y, p), (p, x)];
        assert_eq!(closes_per_leaver(&pairs, &[x, p]), vec![(x, 2), (p, 3)]);
    }
}
