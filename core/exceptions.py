"""
Domain-specific exception hierarchy for clearer error handling across Elara.
"""


class ElaraDomainError(Exception):
    """Base class for known domain errors in Elara."""


class ConfigurationError(ElaraDomainError):
    """Raised when runtime configuration is invalid or incomplete."""


class StateBackendError(ElaraDomainError):
    """Base class for state backend failures."""


class StateBackendUnavailableError(StateBackendError):
    """Raised when the configured state backend cannot be reached."""


class StateEncryptionError(StateBackendError):
    """Raised when state payload encryption/decryption fails."""


class TradingDomainError(ElaraDomainError):
    """Base class for trading-related domain errors."""


class ExchangeUnavailableError(TradingDomainError):
    """Raised when exchange connector is unavailable for required operations."""


class SequenceGapError(TradingDomainError):
    """Raised when WebSocket sequence checks detect a data gap."""


