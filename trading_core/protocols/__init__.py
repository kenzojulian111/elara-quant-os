"""Platform protocol contracts for Elara core data shapes."""

from trading_core.protocols.core_contracts import (
    CORE_CONTRACT_VERSION,
    CoreContractReport,
    PlatformContract,
    build_core_contract_registry,
    validate_core_contracts,
)

__all__ = [
    "CORE_CONTRACT_VERSION",
    "CoreContractReport",
    "PlatformContract",
    "build_core_contract_registry",
    "validate_core_contracts",
]
