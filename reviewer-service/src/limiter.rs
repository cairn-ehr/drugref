use std::{
    collections::{HashMap, VecDeque},
    net::IpAddr,
    sync::Mutex,
    time::{Duration, Instant},
};

/// A small process-local login limiter. Production deployments should also apply an
/// edge/proxy limit, but the service does not rely on deployment configuration alone.
pub struct LoginLimiter {
    attempts: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
    maximum: usize,
    window: Duration,
}

impl Default for LoginLimiter {
    fn default() -> Self {
        Self {
            attempts: Mutex::new(HashMap::new()),
            maximum: 8,
            window: Duration::from_secs(60),
        }
    }
}

impl LoginLimiter {
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
        if !all.contains_key(&address) && all.len() >= 4096 {
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
    use super::LoginLimiter;
    use std::{
        collections::{HashMap, VecDeque},
        net::{IpAddr, Ipv6Addr},
        str::FromStr,
        sync::Mutex,
        time::{Duration, Instant},
    };

    #[test]
    fn ninth_attempt_from_one_address_is_limited() {
        let limiter = LoginLimiter {
            attempts: Mutex::new(HashMap::new()),
            maximum: 8,
            window: Duration::from_secs(60),
        };
        let address = IpAddr::from_str("127.0.0.1").expect("address");
        assert!((0..8).all(|_| limiter.allow(address)));
        assert!(!limiter.allow(address));
    }

    #[test]
    fn address_buckets_are_bounded() {
        let now = Instant::now();
        let attempts = (0..4096)
            .map(|value| {
                (
                    IpAddr::V6(Ipv6Addr::from(value as u128)),
                    VecDeque::from([now]),
                )
            })
            .collect();
        let limiter = LoginLimiter {
            attempts: Mutex::new(attempts),
            maximum: 8,
            window: Duration::from_secs(60),
        };
        assert!(!limiter.allow(IpAddr::V6(Ipv6Addr::from(5000_u128))));
    }
}
