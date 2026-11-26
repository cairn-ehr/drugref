//! Native signing of departure acknowledgements in batches (db/088, #284).
//!
//! The service prepares one challenge per acknowledgement under one signing instant.
//! This core checks that every challenge is bound to the acknowledgement, key and
//! instant requested and re-derives its bytes; the WebView then shows every payload;
//! after confirmation the vault is unlocked once and every payload is signed. As for
//! rulings, the WebView never sees key material and cannot ask for arbitrary bytes to
//! be signed: only the batch retained here at preview time can be signed.

use reqwest::Method;
use reviewer_domain::{
    validate_signing_passphrase, AckSignature, AckSignatureChallenges, AckSignaturePreview,
    AckSignaturePreviewItem, AckSignatureQuery, AckSignatureResult, ReviewSignatureChallenge,
    SubmitAckSignaturesRequest, UnsignedDepartureAck, DEPARTURE_ACK_TARGET_KIND,
    DEPARTURE_ACK_V1_CONTEXT,
};
use sha2::{Digest, Sha256};
use zeroize::Zeroizing;

use crate::{
    accounts::{send_json, AccountClient},
    signing::{encode_hex, sign_locally, SigningClient},
};

const UNSIGNED_PATH: &str = "/v1/departure-acks/unsigned";
const CHALLENGES_PATH: &str = "/v1/departure-ack-signatures/challenges";
const SUBMIT_PATH: &str = "/v1/departure-ack-signatures";

/// One previewed batch retained between preview and confirmation.
pub(crate) struct PendingAckBatch {
    query: AckSignatureQuery,
    signed_at: String,
    digests: Vec<String>,
    payloads: Vec<Vec<u8>>,
}

/// Check every challenge against the request and re-derive its bytes. Returns the
/// preview plus the exact payloads to sign, in query order. PURE.
fn bind_challenges(
    query: &AckSignatureQuery,
    prepared: AckSignatureChallenges,
) -> Result<(AckSignaturePreview, Vec<Vec<u8>>), String> {
    if prepared.challenges.len() != query.ack_ids.len() {
        return Err("review service returned a different number of signing challenges".into());
    }
    let mut items = Vec::with_capacity(query.ack_ids.len());
    let mut payloads = Vec::with_capacity(query.ack_ids.len());
    for (challenge, ack_id) in prepared.challenges.into_iter().zip(&query.ack_ids) {
        if challenge.target_kind != DEPARTURE_ACK_TARGET_KIND
            || challenge.payload_context != DEPARTURE_ACK_V1_CONTEXT
            || challenge.target_id != *ack_id
        {
            return Err("review service returned a signing challenge for another target".into());
        }
        if challenge.signed_at != prepared.signed_at
            || field(&challenge, "signer_key_fingerprint") != Some(&query.key_fingerprint)
            || field(&challenge, "signed_at") != Some(&prepared.signed_at)
        {
            return Err("review service returned an unbound signing challenge".into());
        }
        let payload = challenge.canonical_payload().map_err(|error| error.0)?;
        let digest = encode_hex(&Sha256::digest(&payload));
        if digest != challenge.payload_digest {
            return Err("review service returned a signing payload with the wrong digest".into());
        }
        items.push(AckSignaturePreviewItem {
            ack_id: *ack_id,
            payload_digest: digest,
            fields: challenge.fields,
        });
        payloads.push(payload);
    }
    let preview = AckSignaturePreview {
        payload_context: DEPARTURE_ACK_V1_CONTEXT.into(),
        key_fingerprint: query.key_fingerprint.clone(),
        signed_at: prepared.signed_at,
        items,
    };
    Ok((preview, payloads))
}

/// One named field's rendered value, if the challenge carries it.
fn field<'a>(challenge: &'a ReviewSignatureChallenge, name: &str) -> Option<&'a String> {
    challenge
        .fields
        .iter()
        .find(|field| field.name == name)
        .and_then(|field| field.value.as_ref())
}

/// Load every acknowledgement with no signature the registry trusts.
#[tauri::command]
pub async fn load_unsigned_departure_acks(
    accounts: tauri::State<'_, AccountClient>,
) -> Result<Vec<UnsignedDepartureAck>, String> {
    send_json::<(), _>(&accounts, Method::GET, UNSIGNED_PATH, None, true).await
}

/// Fetch, check and retain one batch of acknowledgement payloads for confirmation.
#[tauri::command]
pub async fn prepare_ack_signatures(
    query: AckSignatureQuery,
    accounts: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, SigningClient>,
) -> Result<AckSignaturePreview, String> {
    let query = query.validate().map_err(|error| error.0)?;
    let prepared: AckSignatureChallenges =
        send_json(&accounts, Method::POST, CHALLENGES_PATH, Some(&query), true).await?;
    let (preview, payloads) = bind_challenges(&query, prepared)?;
    *signing
        .pending_acks
        .lock()
        .map_err(|_| "native signing preview store is unavailable".to_string())? =
        Some(PendingAckBatch {
            query,
            signed_at: preview.signed_at.clone(),
            digests: preview
                .items
                .iter()
                .map(|item| item.payload_digest.clone())
                .collect(),
            payloads,
        });
    Ok(preview)
}

/// Unlock the vault once, sign the confirmed batch, and submit it for verification.
#[tauri::command]
pub async fn complete_ack_signatures(
    query: AckSignatureQuery,
    payload_digests: Vec<String>,
    passphrase: String,
    accounts: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, SigningClient>,
) -> Result<AckSignatureResult, String> {
    let passphrase = Zeroizing::new(passphrase);
    validate_signing_passphrase(&passphrase).map_err(|error| error.0)?;
    let reviewer = accounts.reviewer()?;
    let (signed_at, payloads) = {
        let pending = signing
            .pending_acks
            .lock()
            .map_err(|_| "native signing preview store is unavailable".to_string())?;
        let pending = pending
            .as_ref()
            .filter(|pending| pending.query == query && pending.digests == payload_digests)
            .ok_or_else(|| "prepare these exact signatures again before confirming".to_string())?;
        (pending.signed_at.clone(), pending.payloads.clone())
    };
    let signatures = sign_locally(
        &signing,
        reviewer.reviewer_uuid,
        &passphrase,
        &query.key_fingerprint,
        &payloads,
    )?;
    let input = SubmitAckSignaturesRequest {
        signatures: query
            .ack_ids
            .iter()
            .zip(payload_digests)
            .zip(signatures)
            .map(|((ack_id, payload_digest), signature)| AckSignature {
                ack_id: *ack_id,
                payload_digest,
                signature_hex: encode_hex(&signature),
            })
            .collect(),
        query,
        signed_at,
    };
    let result = send_json(&accounts, Method::POST, SUBMIT_PATH, Some(&input), true).await?;
    // Best-effort, and only the ack batch (an open ruling preview stays): the batch
    // IS recorded, so a poisoned store must not report it as failed. A poisoned
    // store already refuses every later confirmation above.
    if let Ok(mut pending) = signing.pending_acks.lock() {
        *pending = None;
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use reviewer_domain::{
        AckSignatureChallenges, AckSignatureQuery, CanonicalField, ReviewSignatureChallenge,
        GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS,
    };
    use sha2::{Digest, Sha256};

    use super::bind_challenges;
    use crate::signing::encode_hex;

    const SIGNED_AT: &str = "2026-11-26T00:00:00.000000Z";

    fn query() -> AckSignatureQuery {
        AckSignatureQuery {
            ack_ids: vec![4, 9],
            key_fingerprint: "a".repeat(64),
        }
    }

    /// A well-formed challenge for one ack, bound to `fingerprint`.
    fn challenge(ack_id: i64, fingerprint: &str) -> ReviewSignatureChallenge {
        let fields = GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS
            .iter()
            .map(|name| CanonicalField {
                name: (*name).into(),
                value: Some(match *name {
                    "signer_key_fingerprint" => fingerprint.into(),
                    "signed_at" => SIGNED_AT.into(),
                    _ => format!("{name} of {ack_id}"),
                }),
            })
            .collect();
        let mut challenge = ReviewSignatureChallenge {
            target_kind: "graded_pair_departure_ack".into(),
            target_id: ack_id,
            payload_context: "graded_pair_departure_ack/v1".into(),
            fields,
            payload_digest: String::new(),
            signed_at: SIGNED_AT.into(),
        };
        challenge.payload_digest = encode_hex(&Sha256::digest(
            challenge.canonical_payload().expect("payload"),
        ));
        challenge
    }

    fn prepared(challenges: Vec<ReviewSignatureChallenge>) -> AckSignatureChallenges {
        AckSignatureChallenges {
            signed_at: SIGNED_AT.into(),
            challenges,
        }
    }

    #[test]
    fn a_bound_batch_previews_every_payload_in_query_order() {
        let fp = "a".repeat(64);
        let (preview, payloads) = bind_challenges(
            &query(),
            prepared(vec![challenge(4, &fp), challenge(9, &fp)]),
        )
        .expect("bound");
        assert_eq!(
            preview.items.iter().map(|i| i.ack_id).collect::<Vec<_>>(),
            vec![4, 9]
        );
        assert_eq!(payloads.len(), 2);
        assert_eq!(
            preview.items[1].fields.len(),
            GRADED_PAIR_DEPARTURE_ACK_V1_FIELDS.len()
        );
    }

    /// Each way a service could slip another payload into the batch is refused.
    #[test]
    fn a_substituted_or_unbound_challenge_is_refused() {
        let fp = "a".repeat(64);
        let refused = |challenges| bind_challenges(&query(), prepared(challenges)).is_err();
        assert!(refused(vec![challenge(4, &fp)]));
        assert!(refused(vec![challenge(9, &fp), challenge(4, &fp)]));
        assert!(refused(vec![
            challenge(4, &fp),
            challenge(9, &"b".repeat(64))
        ]));
        let mut wrong_digest = challenge(9, &fp);
        wrong_digest.payload_digest = "0".repeat(64);
        assert!(refused(vec![challenge(4, &fp), wrong_digest]));
        let mut ruling = challenge(9, &fp);
        ruling.target_kind = "curated_interaction".into();
        assert!(refused(vec![challenge(4, &fp), ruling]));
    }
}
