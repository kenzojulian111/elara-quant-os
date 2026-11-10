pub const PROTOCOL_VERSION: u16 = 1;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeHandshake {
    pub protocol_version: u16,
    pub capabilities: Vec<String>,
}

impl RuntimeHandshake {
    pub fn compatible(&self) -> bool {
        self.protocol_version == PROTOCOL_VERSION
            && self.capabilities.iter().any(|item| item == "event_stream_v1")
            && self.capabilities.iter().any(|item| item == "replay_executor_v1")
    }
}

pub fn normalize_symbol(symbol: &str) -> String {
    symbol.trim().to_ascii_uppercase().replace('-', "")
}

pub fn next_sequence(current: u64) -> u64 {
    current.saturating_add(1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handshake_requires_protocol_and_capabilities() {
        let handshake = RuntimeHandshake {
            protocol_version: PROTOCOL_VERSION,
            capabilities: vec!["event_stream_v1".into(), "replay_executor_v1".into()],
        };
        assert!(handshake.compatible());
    }

    #[test]
    fn symbol_normalization_is_deterministic() {
        assert_eq!(normalize_symbol(" btc-usdt "), "BTCUSDT");
    }
}
