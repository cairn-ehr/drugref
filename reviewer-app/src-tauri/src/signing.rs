//! Native-only encrypted signing vault and detached-signature workflow.
//!
//! The WebView can request enrolment, preview, and confirmation, but cannot read key
//! material or ask the Stronghold engine to sign arbitrary bytes. The encrypted vault
//! is opened only for one operation and its key provider is dropped immediately.

use std::{
    convert::TryFrom,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Mutex,
};

use argon2::{Algorithm, Argon2, Params, Version};
use iota_stronghold::{
    procedures::{Ed25519Sign, KeyType, PublicKey, Slip10Generate, StrongholdProcedure},
    Client, KeyProvider, Location, SnapshotPath, Stronghold,
};
use rand_core::{OsRng, RngCore};
use reqwest::Method;
use reviewer_domain::{
    validate_signing_passphrase, DeviceSigningStatus, EnrolSigningKeyRequest,
    PendingReviewSignature, ReplaceSigningKeyRequest, ReviewDecisionRecord,
    ReviewSignatureChallenge, ReviewSignaturePreview, ReviewSignatureQuery, SigningKeyReplacement,
    SigningKeyStatus, SigningKeySummary, SubmitReviewSignatureRequest,
};
use sha2::{Digest, Sha256};
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::accounts::{send_json, AccountClient};

const SIGNING_KEYS_PATH: &str = "/v1/signing-keys/current";
const REVIEW_SIGNATURE_PATH: &str = "/v1/review-signature";
const PENDING_SIGNATURES_PATH: &str = "/v1/pending-signatures";
const VAULT_FILE_PREFIX: &str = "reviewer-signing-";
const VAULT_FILE_SUFFIX: &str = ".hold";
const SALT_FILE_SUFFIX: &str = ".salt";
const FINGERPRINT_FILE_SUFFIX: &str = ".fingerprint";
const STRONGHOLD_CLIENT: &[u8] = b"drugref-reviewer/v1";
const STRONGHOLD_VAULT: &[u8] = b"clinical-signing";
const STRONGHOLD_RECORD: &[u8] = b"ed25519";
const KEY_BYTES: usize = 32;
const SALT_BYTES: usize = 32;
const ARGON2_MEMORY_KIB: u32 = 19 * 1024;
const ARGON2_ITERATIONS: u32 = 2;
const ARGON2_LANES: u32 = 1;

/// One exact challenge retained after preview and before local confirmation.
struct PendingSignature {
    query: ReviewSignatureQuery,
    challenge: ReviewSignatureChallenge,
    payload: Vec<u8>,
}

/// Native signing state managed by Tauri without exposing a generic vault plugin.
pub struct SigningClient {
    vault_directory: PathBuf,
    pending: Mutex<Option<PendingSignature>>,
}

impl SigningClient {
    /// Construct a native signing client rooted in the application's local data path.
    pub fn new(vault_directory: PathBuf) -> Self {
        Self {
            vault_directory,
            pending: Mutex::new(None),
        }
    }

    /// Remove an unconfirmed payload when its authenticated session ends.
    pub fn clear_pending(&self) -> Result<(), String> {
        *self
            .pending
            .lock()
            .map_err(|_| "native signing preview store is unavailable".to_string())? = None;
        Ok(())
    }

    /// Resolve one reviewer's encrypted snapshot without accepting a client path.
    fn paths(&self, reviewer_uuid: Uuid) -> VaultPaths {
        let stem = format!("{VAULT_FILE_PREFIX}{reviewer_uuid}");
        VaultPaths {
            snapshot: self
                .vault_directory
                .join(format!("{stem}{VAULT_FILE_SUFFIX}")),
            salt: self
                .vault_directory
                .join(format!("{stem}{SALT_FILE_SUFFIX}")),
            fingerprint: self
                .vault_directory
                .join(format!("{stem}{FINGERPRINT_FILE_SUFFIX}")),
        }
    }
}

/// Stronghold snapshot and independent Argon2 salt paths for one reviewer.
struct VaultPaths {
    snapshot: PathBuf,
    salt: PathBuf,
    fingerprint: PathBuf,
}

/// Open encrypted Stronghold state plus the key provider required to commit it.
struct OpenVault {
    stronghold: Stronghold,
    snapshot: SnapshotPath,
    key_provider: KeyProvider,
}

impl OpenVault {
    /// Open an existing snapshot or initialise empty in-memory Stronghold state.
    fn open(paths: &VaultPaths, passphrase: &str) -> Result<Self, String> {
        // Stronghold's default snapshot work factor assumes its input is a weak,
        // password-derived key. This client supplies an independent Argon2id
        // 256-bit key, so a second exponential password KDF would add latency
        // without strengthening that already-derived snapshot key.
        iota_stronghold::engine::snapshot::try_set_encrypt_work_factor(0)
            .map_err(|error| format!("cannot configure signing-vault encryption: {error}"))?;
        let salt = load_or_create_salt(paths)?;
        let mut derived = Zeroizing::new([0_u8; KEY_BYTES]);
        let params = Params::new(
            ARGON2_MEMORY_KIB,
            ARGON2_ITERATIONS,
            ARGON2_LANES,
            Some(KEY_BYTES),
        )
        .map_err(|error| format!("cannot configure signing-vault KDF: {error}"))?;
        Argon2::new(Algorithm::Argon2id, Version::V0x13, params)
            .hash_password_into(passphrase.as_bytes(), &salt, &mut *derived)
            .map_err(|_| "cannot derive the signing-vault key".to_string())?;
        let key_provider = KeyProvider::try_from(Zeroizing::new(derived.to_vec()))
            .map_err(|error| format!("cannot initialise signing-vault key: {error}"))?;
        let stronghold = Stronghold::default();
        let snapshot = SnapshotPath::from_path(&paths.snapshot);
        if paths.snapshot.exists() {
            stronghold
                .load_snapshot(&key_provider, &snapshot)
                .map_err(|_| {
                    "cannot unlock the local signing vault; check its passphrase".to_string()
                })?;
        }
        Ok(Self {
            stronghold,
            snapshot,
            key_provider,
        })
    }

    /// Load the fixed native client, creating it only for a genuinely new snapshot.
    fn client(&self, create: bool) -> Result<Client, String> {
        if create {
            self.stronghold
                .create_client(STRONGHOLD_CLIENT)
                .map_err(|error| format!("cannot create signing-vault client: {error}"))
        } else {
            self.stronghold
                .load_client(STRONGHOLD_CLIENT)
                .map_err(|_| "local signing vault is incomplete".to_string())
        }
    }

    /// Encrypt and atomically commit the current Stronghold snapshot.
    fn save(&self, path: &Path) -> Result<(), String> {
        self.stronghold
            .commit_with_keyprovider(&self.snapshot, &self.key_provider)
            .map_err(|error| format!("cannot save the local signing vault: {error}"))?;
        restrict_file_permissions(path)
    }
}

/// Return service enrolments alongside local encrypted-vault availability.
#[tauri::command]
pub async fn signing_status(
    accounts: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, SigningClient>,
) -> Result<DeviceSigningStatus, String> {
    let reviewer = accounts.reviewer()?;
    let paths = signing.paths(reviewer.reviewer_uuid);
    let status: SigningKeyStatus =
        send_json::<(), _>(&accounts, Method::GET, SIGNING_KEYS_PATH, None, true).await?;
    Ok(DeviceSigningStatus {
        local_vault_exists: paths.snapshot.exists(),
        local_key_fingerprint: read_local_fingerprint(&paths)?,
        keys: status.keys,
    })
}

/// Load unsigned current revisions through the authenticated native boundary.
#[tauri::command]
pub async fn load_pending_signatures(
    accounts: tauri::State<'_, AccountClient>,
) -> Result<Vec<PendingReviewSignature>, String> {
    send_json::<(), _>(&accounts, Method::GET, PENDING_SIGNATURES_PATH, None, true).await
}

/// Generate or reopen the local key and enrol only its public half with the service.
#[tauri::command]
pub async fn enrol_local_signing_key(
    passphrase: String,
    accounts: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, SigningClient>,
) -> Result<SigningKeySummary, String> {
    let passphrase = Zeroizing::new(passphrase);
    validate_signing_passphrase(&passphrase).map_err(|error| error.0)?;
    let reviewer = accounts.reviewer()?;
    let paths = signing.paths(reviewer.reviewer_uuid);
    fs::create_dir_all(&signing.vault_directory)
        .map_err(|error| format!("cannot create the local signing-vault directory: {error}"))?;
    let existing = paths.snapshot.exists();
    let vault = OpenVault::open(&paths, &passphrase)?;
    let client = vault.client(!existing)?;
    if !existing {
        execute(
            &client,
            StrongholdProcedure::Slip10Generate(Slip10Generate {
                output: private_key_location(),
                size_bytes: Some(KEY_BYTES),
            }),
            "generate the local signing key",
        )?;
    }
    let public_key = public_key(&client)?;
    vault.save(&paths.snapshot)?;
    let fingerprint = encode_hex(&Sha256::digest(&public_key));
    write_local_fingerprint(&paths, &fingerprint)?;
    let input = EnrolSigningKeyRequest {
        public_key_hex: encode_hex(&public_key),
    };
    send_json(
        &accounts,
        Method::POST,
        SIGNING_KEYS_PATH,
        Some(&input),
        true,
    )
    .await
}

/// Retire the current public enrolment, then delete its fixed local vault files.
///
/// No passphrase is required because this is the recovery path for a lost passphrase.
/// The service authenticates ownership and records time-scoped rotation before native
/// deletion, while an idempotent retry can finish cleanup after a filesystem failure.
#[tauri::command]
pub async fn replace_local_signing_key(
    accounts: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, SigningClient>,
) -> Result<SigningKeyReplacement, String> {
    let reviewer = accounts.reviewer()?;
    let paths = signing.paths(reviewer.reviewer_uuid);
    let key_fingerprint = read_local_fingerprint(&paths)?
        .ok_or_else(|| "this reviewer has no local signing key to replace".to_string())?;
    let input = ReplaceSigningKeyRequest { key_fingerprint };
    let replacement = send_json(
        &accounts,
        Method::DELETE,
        SIGNING_KEYS_PATH,
        Some(&input),
        true,
    )
    .await?;
    remove_vault_files(&paths)?;
    signing.clear_pending()?;
    Ok(replacement)
}

/// Fetch, validate, and retain one exact canonical payload for human confirmation.
#[tauri::command]
pub async fn prepare_review_signature(
    query: ReviewSignatureQuery,
    accounts: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, SigningClient>,
) -> Result<ReviewSignaturePreview, String> {
    let query = query.validate().map_err(|error| error.0)?;
    let response = accounts
        .http
        .get(accounts.endpoint(REVIEW_SIGNATURE_PATH)?)
        .bearer_auth(accounts.token()?)
        .query(&query)
        .send()
        .await
        .map_err(|error| format!("cannot reach the review service: {error}"))?;
    let challenge: ReviewSignatureChallenge = crate::accounts::response_json(response).await?;
    validate_challenge_binding(&query, &challenge)?;
    let payload = challenge.canonical_payload().map_err(|error| error.0)?;
    let digest = encode_hex(&Sha256::digest(&payload));
    if digest != challenge.payload_digest {
        return Err("review service returned a signing payload with the wrong digest".into());
    }
    let preview = confirmation_preview(&query, &challenge, digest);
    *signing
        .pending
        .lock()
        .map_err(|_| "native signing preview store is unavailable".to_string())? =
        Some(PendingSignature {
            query,
            challenge,
            payload,
        });
    Ok(preview)
}

/// Preserve every validated named value for human confirmation without exposing bytes.
fn confirmation_preview(
    query: &ReviewSignatureQuery,
    challenge: &ReviewSignatureChallenge,
    payload_digest: String,
) -> ReviewSignaturePreview {
    ReviewSignaturePreview {
        revision_id: query.revision_id,
        payload_context: challenge.payload_context.clone(),
        payload_digest,
        key_fingerprint: query.key_fingerprint.clone(),
        signed_at: challenge.signed_at.clone(),
        field_count: challenge.fields.len(),
        fields: challenge.fields.clone(),
    }
}

/// Unlock the local vault, sign the confirmed bytes, and submit for verification.
#[tauri::command]
pub async fn complete_review_signature(
    query: ReviewSignatureQuery,
    payload_digest: String,
    passphrase: String,
    accounts: tauri::State<'_, AccountClient>,
    signing: tauri::State<'_, SigningClient>,
) -> Result<ReviewDecisionRecord, String> {
    let passphrase = Zeroizing::new(passphrase);
    validate_signing_passphrase(&passphrase).map_err(|error| error.0)?;
    let reviewer = accounts.reviewer()?;
    let (challenge, payload) = {
        let pending = signing
            .pending
            .lock()
            .map_err(|_| "native signing preview store is unavailable".to_string())?;
        let pending = pending
            .as_ref()
            .filter(|pending| {
                pending.query == query && pending.challenge.payload_digest == payload_digest
            })
            .ok_or_else(|| "prepare this exact signature again before confirming it".to_string())?;
        (clone_challenge(&pending.challenge), pending.payload.clone())
    };
    let paths = signing.paths(reviewer.reviewer_uuid);
    if !paths.snapshot.exists() {
        return Err("this reviewer has no local signing vault on this device".into());
    }
    let vault = OpenVault::open(&paths, &passphrase)?;
    let client = vault.client(false)?;
    let public_key = public_key(&client)?;
    if encode_hex(&Sha256::digest(&public_key)) != query.key_fingerprint {
        return Err("the local signing key does not match the confirmed enrolment".into());
    }
    let signature = execute(
        &client,
        StrongholdProcedure::Ed25519Sign(Ed25519Sign {
            private_key: private_key_location(),
            msg: payload,
        }),
        "sign the confirmed review payload",
    )?;
    let input = SubmitReviewSignatureRequest {
        query,
        signed_at: challenge.signed_at,
        payload_digest,
        signature_hex: encode_hex(&signature),
    };
    let record = send_json(
        &accounts,
        Method::POST,
        REVIEW_SIGNATURE_PATH,
        Some(&input),
        true,
    )
    .await?;
    signing.clear_pending()?;
    Ok(record)
}

/// Recreate a challenge without adding Clone to the public contract unnecessarily.
fn clone_challenge(challenge: &ReviewSignatureChallenge) -> ReviewSignatureChallenge {
    ReviewSignatureChallenge {
        target_kind: challenge.target_kind.clone(),
        target_id: challenge.target_id,
        payload_context: challenge.payload_context.clone(),
        fields: challenge.fields.clone(),
        payload_digest: challenge.payload_digest.clone(),
        signed_at: challenge.signed_at.clone(),
    }
}

/// Bind the server challenge back to the target, revision, key, and instant requested.
fn validate_challenge_binding(
    query: &ReviewSignatureQuery,
    challenge: &ReviewSignatureChallenge,
) -> Result<(), String> {
    let expected_kind = match query.kind {
        reviewer_domain::ReviewKind::InteractionRule => "curated_interaction",
        reviewer_domain::ReviewKind::ConditionContradiction => "curated_condition",
    };
    if challenge.target_kind != expected_kind || challenge.target_id != query.revision_id {
        return Err("review service returned a signing challenge for another revision".into());
    }
    let fingerprint = challenge
        .fields
        .iter()
        .find(|field| field.name == "signer_key_fingerprint")
        .and_then(|field| field.value.as_deref());
    let signed_at = challenge
        .fields
        .iter()
        .find(|field| field.name == "signed_at")
        .and_then(|field| field.value.as_deref());
    if fingerprint != Some(query.key_fingerprint.as_str())
        || signed_at != Some(challenge.signed_at.as_str())
    {
        return Err("review service returned an unbound signing challenge".into());
    }
    Ok(())
}

/// Derive the Ed25519 public key while the private bytes remain inside Stronghold.
fn public_key(client: &Client) -> Result<Vec<u8>, String> {
    execute(
        client,
        StrongholdProcedure::PublicKey(PublicKey {
            ty: KeyType::Ed25519,
            private_key: private_key_location(),
        }),
        "derive the local signing public key",
    )
}

/// Execute one constrained Stronghold procedure and return only its public output.
fn execute(
    client: &Client,
    procedure: StrongholdProcedure,
    action: &str,
) -> Result<Vec<u8>, String> {
    client
        .execute_procedure(procedure)
        .map(Into::into)
        .map_err(|error| format!("cannot {action}: {error}"))
}

/// Return the one fixed private-key location that no WebView input can alter.
fn private_key_location() -> Location {
    Location::generic(STRONGHOLD_VAULT, STRONGHOLD_RECORD)
}

/// Read the existing KDF salt or create it once with restrictive local permissions.
fn load_or_create_salt(paths: &VaultPaths) -> Result<[u8; SALT_BYTES], String> {
    if paths.snapshot.exists() && !paths.salt.exists() {
        return Err(
            "the local signing vault salt is missing; refusing unrecoverable regeneration".into(),
        );
    }
    let mut salt = [0_u8; SALT_BYTES];
    if paths.salt.exists() {
        File::open(&paths.salt)
            .and_then(|mut file| file.read_exact(&mut salt))
            .map_err(|error| format!("cannot read the signing-vault salt: {error}"))?;
        return Ok(salt);
    }
    OsRng.fill_bytes(&mut salt);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
        .open(&paths.salt)
        .and_then(|mut file| file.write_all(&salt))
        .map_err(|error| format!("cannot create the signing-vault salt: {error}"))?;
    Ok(salt)
}

/// Restrict a vault file to its owner on Unix; encryption remains cross-platform.
fn restrict_file_permissions(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("cannot restrict signing-vault permissions: {error}"))?;
    }
    Ok(())
}

/// Read the non-secret public fingerprint used to select the matching enrolment.
fn read_local_fingerprint(paths: &VaultPaths) -> Result<Option<String>, String> {
    if !paths.fingerprint.exists() {
        return Ok(None);
    }
    let value = fs::read_to_string(&paths.fingerprint)
        .map_err(|error| format!("cannot read the local signing-key fingerprint: {error}"))?;
    let value = value.trim();
    if value.len() != KEY_BYTES * 2
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err("the local signing-key fingerprint metadata is invalid".into());
    }
    Ok(Some(value.into()))
}

/// Persist only the public fingerprint beside the encrypted Stronghold snapshot.
fn write_local_fingerprint(paths: &VaultPaths, fingerprint: &str) -> Result<(), String> {
    let mut options = OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
        .open(&paths.fingerprint)
        .and_then(|mut file| file.write_all(fingerprint.as_bytes()))
        .map_err(|error| format!("cannot save the local signing-key fingerprint: {error}"))?;
    restrict_file_permissions(&paths.fingerprint)
}

/// Remove only the three fixed files belonging to the authenticated reviewer.
///
/// The public fingerprint is removed last so a retry can still identify an already
/// retired service enrolment if an earlier filesystem operation fails.
fn remove_vault_files(paths: &VaultPaths) -> Result<(), String> {
    remove_file_if_present(&paths.snapshot, "encrypted signing vault")?;
    remove_file_if_present(&paths.salt, "signing-vault salt")?;
    remove_file_if_present(&paths.fingerprint, "local signing-key fingerprint")
}

/// Delete one fixed local file while treating an already-absent file as success.
fn remove_file_if_present(path: &Path, label: &str) -> Result<(), String> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(format!("cannot remove the {label}: {error}")),
    }
}

/// Encode bytes as canonical lowercase hexadecimal without exposing private bytes.
fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

#[cfg(test)]
mod tests {
    use super::{
        confirmation_preview, encode_hex, execute, private_key_location, public_key,
        remove_vault_files, validate_challenge_binding, OpenVault, SigningClient, KEY_BYTES,
    };
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};
    use iota_stronghold::procedures::{Ed25519Sign, Slip10Generate, StrongholdProcedure};
    use reviewer_domain::{
        CanonicalField, ReviewKind, ReviewSignatureChallenge, ReviewSignatureQuery,
    };
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };
    use uuid::Uuid;

    /// Refuse a validly-shaped challenge that binds a different key fingerprint.
    #[test]
    fn challenge_binding_catches_key_substitution() {
        let query = ReviewSignatureQuery {
            kind: ReviewKind::InteractionRule,
            target_key: "MOIETY:a/CLASS:b/CI_AXIS:CI_with".into(),
            revision_id: 7,
            key_fingerprint: "a".repeat(64),
        };
        let challenge = ReviewSignatureChallenge {
            target_kind: "curated_interaction".into(),
            target_id: 7,
            payload_context: "curated_interaction/v1".into(),
            fields: vec![
                CanonicalField {
                    name: "signer_key_fingerprint".into(),
                    value: Some("b".repeat(64)),
                },
                CanonicalField {
                    name: "signed_at".into(),
                    value: Some("2026-08-18T00:00:00.000000Z".into()),
                },
            ],
            payload_digest: encode_hex(&[0; 32]),
            signed_at: "2026-08-18T00:00:00.000000Z".into(),
        };
        assert!(validate_challenge_binding(&query, &challenge).is_err());
    }

    /// Show every already-validated field without returning the canonical byte buffer.
    #[test]
    fn confirmation_preview_preserves_exact_field_order_and_values() {
        let query = ReviewSignatureQuery {
            kind: ReviewKind::InteractionRule,
            target_key: "MOIETY:a/CLASS:b/CI_AXIS:CI_with".into(),
            revision_id: 7,
            key_fingerprint: "a".repeat(64),
        };
        let fields = vec![CanonicalField {
            name: "management".into(),
            value: Some("Monitor the complete clinical addition.".into()),
        }];
        let challenge = ReviewSignatureChallenge {
            target_kind: "curated_interaction".into(),
            target_id: 7,
            payload_context: "curated_interaction/v1".into(),
            fields: fields.clone(),
            payload_digest: "b".repeat(64),
            signed_at: "2026-08-18T00:00:00.000000Z".into(),
        };

        let preview = confirmation_preview(&query, &challenge, challenge.payload_digest.clone());

        assert_eq!(preview.fields, fields);
        assert_eq!(preview.field_count, preview.fields.len());
    }

    /// Persist an encrypted key, reject a wrong passphrase, and sign after reopening.
    #[test]
    fn stronghold_vault_survives_restart_without_exposing_private_bytes() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "drugref-reviewer-stronghold-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir(&directory).expect("test vault directory");
        let signing = SigningClient::new(directory.clone());
        let paths = signing.paths(Uuid::nil());
        let passphrase = "test signing passphrase";

        let vault = OpenVault::open(&paths, passphrase).expect("new vault");
        let client = vault.client(true).expect("new client");
        execute(
            &client,
            StrongholdProcedure::Slip10Generate(Slip10Generate {
                output: private_key_location(),
                size_bytes: Some(KEY_BYTES),
            }),
            "generate test key",
        )
        .expect("generated key");
        let expected_public_key = public_key(&client).expect("public key");
        vault.save(&paths.snapshot).expect("saved vault");

        assert!(OpenVault::open(&paths, "wrong signing passphrase").is_err());
        let reopened = OpenVault::open(&paths, passphrase).expect("reopened vault");
        let client = reopened.client(false).expect("loaded client");
        let payload = b"drugref signing persistence test".to_vec();
        let signature = execute(
            &client,
            StrongholdProcedure::Ed25519Sign(Ed25519Sign {
                private_key: private_key_location(),
                msg: payload.clone(),
            }),
            "sign test payload",
        )
        .expect("signature");
        let public_key: [u8; KEY_BYTES] =
            expected_public_key.try_into().expect("public key length");
        VerifyingKey::from_bytes(&public_key)
            .expect("valid public key")
            .verify(
                &payload,
                &Signature::from_slice(&signature).expect("valid signature length"),
            )
            .expect("signature verifies");
        fs::remove_dir_all(directory).expect("remove test vault directory");
    }

    /// Delete every fixed vault file without accepting a WebView-controlled path.
    #[test]
    fn replacement_removes_only_the_reviewers_fixed_vault_files() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let directory = std::env::temp_dir().join(format!(
            "drugref-reviewer-replacement-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir(&directory).expect("test vault directory");
        let signing = SigningClient::new(directory.clone());
        let paths = signing.paths(Uuid::nil());
        fs::write(&paths.snapshot, b"snapshot").expect("snapshot");
        fs::write(&paths.salt, b"salt").expect("salt");
        fs::write(&paths.fingerprint, b"fingerprint").expect("fingerprint");
        let unrelated = directory.join("keep-me");
        fs::write(&unrelated, b"unrelated").expect("unrelated file");

        remove_vault_files(&paths).expect("vault cleanup");
        assert!(!paths.snapshot.exists());
        assert!(!paths.salt.exists());
        assert!(!paths.fingerprint.exists());
        assert!(unrelated.exists());

        remove_vault_files(&paths).expect("idempotent cleanup");
        fs::remove_dir_all(directory).expect("remove test vault directory");
    }
}
