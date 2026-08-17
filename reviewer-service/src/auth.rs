use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};
use sha2::{Digest, Sha256};
use std::sync::OnceLock;

use crate::error::AppError;

pub fn hash_password(password: &str) -> Result<String, AppError> {
    let salt = SaltString::generate(&mut OsRng);
    Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map(|hash| hash.to_string())
        .map_err(|_| AppError::internal("password hashing failed"))
}

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

pub fn new_session_token() -> String {
    use argon2::password_hash::rand_core::RngCore;

    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    encode_hex(&bytes)
}

pub fn token_digest(token: &str) -> [u8; 32] {
    Sha256::digest(token.as_bytes()).into()
}

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
    use super::{hash_password, new_session_token, token_digest, verify_password};

    #[test]
    fn passwords_are_argon2id_and_verify_without_storing_plaintext() {
        let hash = hash_password("a sufficiently long password").expect("hash");
        assert!(hash.starts_with("$argon2id$"));
        assert!(verify_password("a sufficiently long password", &hash));
        assert!(!verify_password("wrong password", &hash));
        assert!(!hash.contains("sufficiently"));
    }

    #[test]
    fn session_tokens_are_random_hex_and_digest_to_32_bytes() {
        let first = new_session_token();
        let second = new_session_token();
        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|byte| byte.is_ascii_hexdigit()));
        assert_ne!(first, second);
        assert_eq!(token_digest(&first).len(), 32);
    }
}
