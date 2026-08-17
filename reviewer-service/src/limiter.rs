//! Bounded, process-local defence against repeated login attempts.

use std::{
    collections::{HashMap, VecDeque},
    net::IpAddr,
    sync::Mutex,
    time::{Duration, Instant},
};

const DEFAULT_MAXIMUM_ATTEMPTS: usize = 8;
const DEFAULT_WINDOW_SECONDS: u64 = 60;
const MAXIMUM_ADDRESS_BUCKETS: usize = 4_096;

/// A small process-local login limiter. Production deployments should also apply an
/// edge/proxy limit, but the service does not rely on deployment configuration alone.
pub struct LoginLimiter {
    attempts: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
    maximum: usize,
    window: Duration,
}

impl Default for LoginLimiter {
    /// Construct the documented default attempt window and bucket bounds.
    fn default() -> Self {
        Self {
            attempts: Mutex::new(HashMap::new()),
            maximum: DEFAULT_MAXIMUM_ATTEMPTS,
            window: Duration::from_secs(DEFAULT_WINDOW_SECONDS),
        }
    }
}

impl LoginLimiter {
    /// Record one attempt and return whether the source address remains permitted.
    pub fn allow(&self, address: IpAddr) -> bool {
        let now = Instant::now();
        let mut all = self
            .attempts
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        // An attacker can vary source addresses too. Remove expired address buckets
        // and bound the live map so the limiter cannot itself become an unbounded
        // memory allocation. Edge rate limiting remains required in production.
        all.retain(|_, bucket| {
            bucket
                .back()
                .is_some_and(|attempt| now.duration_since(*attempt) < self.window)
        });
        if !all.contains_key(&address) && all.len() >= MAXIMUM_ADDRESS_BUCKETS {
            return false;
        }
        let attempts = all.entry(address).or_default();
        while attempts
            .front()
            .is_some_and(|attempt| now.duration_since(*attempt) >= self.window)
        {
            attempts.pop_front();
        }
        if attempts.len() >= self.maximum {
            return false;
        }
        attempts.push_back(now);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::{
        LoginLimiter, DEFAULT_MAXIMUM_ATTEMPTS, DEFAULT_WINDOW_SECONDS, MAXIMUM_ADDRESS_BUCKETS,
    };
    use std::{
        collections::{HashMap, VecDeque},
        net::{IpAddr, Ipv6Addr},
        str::FromStr,
        sync::Mutex,
        time::{Duration, Instant},
    };

    const ADDRESS_OUTSIDE_BUCKET_BOUND: u128 = 5_000;

    /// Permit the named attempt budget and reject the immediately following attempt.
    #[test]
    fn ninth_attempt_from_one_address_is_limited() {
        let limiter = LoginLimiter {
            attempts: Mutex::new(HashMap::new()),
            maximum: DEFAULT_MAXIMUM_ATTEMPTS,
            window: Duration::from_secs(DEFAULT_WINDOW_SECONDS),
        };
        let address = IpAddr::from_str("127.0.0.1").expect("address");
        assert!((0..DEFAULT_MAXIMUM_ATTEMPTS).all(|_| limiter.allow(address)));
        assert!(!limiter.allow(address));
    }

    /// Reject new source addresses once the named live bucket bound is reached.
    #[test]
    fn address_buckets_are_bounded() {
        let now = Instant::now();
        let attempts = (0..MAXIMUM_ADDRESS_BUCKETS)
            .map(|value| {
                (
                    IpAddr::V6(Ipv6Addr::from(value as u128)),
                    VecDeque::from([now]),
                )
            })
            .collect();
        let limiter = LoginLimiter {
            attempts: Mutex::new(attempts),
            maximum: DEFAULT_MAXIMUM_ATTEMPTS,
            window: Duration::from_secs(DEFAULT_WINDOW_SECONDS),
        };
        assert!(!limiter.allow(IpAddr::V6(Ipv6Addr::from(ADDRESS_OUTSIDE_BUCKET_BOUND,))));
    }
}
