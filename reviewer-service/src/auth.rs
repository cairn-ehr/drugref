//! Password hashing and opaque session-token primitives.

use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

use crate::error::AppError;

const SESSION_TOKEN_BYTES: usize = 32;
const HEX_CHARACTERS_PER_BYTE: usize = 2;
const NIBBLE_BITS: u8 = 4;
const LOW_NIBBLE_MASK: u8 = 0x0f;
const HEX_ALPHABET_LENGTH: usize = 16;

/// Hash a raw password with a fresh salt using the configured Argon2id defaults.
pub fn hash_password(password: &str) -> Result<String, AppError> {
    let salt = SaltString::generate(&mut OsRng);
    Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map(|hash| hash.to_string())
        .map_err(|_| AppError::internal("password hashing failed"))
}

/// Verify a raw password against one encoded Argon2 password hash.
pub fn verify_password(password: &str, encoded: &str) -> bool {
    PasswordHash::new(encoded).ok().is_some_and(|hash| {
        Argon2::default()
            .verify_password(password.as_bytes(), &hash)
            .is_ok()
    })
}

/// Verify against a real Argon2id hash even when the username is absent. This keeps
/// the externally visible failure path from becoming a cheap username oracle.
pub fn dummy_password_hash() -> &'static str {
    static HASH: OnceLock<String> = OnceLock::new();
    HASH.get_or_init(|| hash_password("drugref missing-account sentinel").expect("valid sentinel"))
}

/// Generate a cryptographically random session token encoded as lowercase hex.
pub fn new_session_token() -> String {
    use argon2::password_hash::rand_core::RngCore;

    let mut bytes = [0_u8; SESSION_TOKEN_BYTES];
    OsRng.fill_bytes(&mut bytes);
    encode_hex(&bytes)
}

/// Produce the fixed-width SHA-256 digest stored for an opaque session token.
pub fn token_digest(token: &str) -> [u8; SESSION_TOKEN_BYTES] {
    Sha256::digest(token.as_bytes()).into()
}

/// Encode bytes as lowercase hexadecimal without allocating intermediate strings.
fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; HEX_ALPHABET_LENGTH] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * HEX_CHARACTERS_PER_BYTE);
    for byte in bytes {
        output.push(HEX[(byte >> NIBBLE_BITS) as usize] as char);
        output.push(HEX[(byte & LOW_NIBBLE_MASK) as usize] as char);
    }
    output
}

#[cfg(test)]
mod tests {
    use super::{
        hash_password, new_session_token, token_digest, verify_password, HEX_CHARACTERS_PER_BYTE,
        SESSION_TOKEN_BYTES,
    };

    /// Confirm passwords use Argon2id and never appear inside their encoded hashes.
    #[test]
    fn passwords_are_argon2id_and_verify_without_storing_plaintext() {
        let hash = hash_password("a sufficiently long password").expect("hash");
        assert!(hash.starts_with("$argon2id$"));
        assert!(verify_password("a sufficiently long password", &hash));
        assert!(!verify_password("wrong password", &hash));
        assert!(!hash.contains("sufficiently"));
    }

    /// Confirm session tokens are random fixed-width hex with fixed-width digests.
    #[test]
    fn session_tokens_are_random_hex_and_digest_to_32_bytes() {
        let first = new_session_token();
        let second = new_session_token();
        assert_eq!(first.len(), SESSION_TOKEN_BYTES * HEX_CHARACTERS_PER_BYTE);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_ne!(first, second);
        assert_eq!(token_digest(&first).len(), SESSION_TOKEN_BYTES);
    }
}
