from __future__ import annotations

import json
import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class SignedArtifact:
    artifact_type: str
    payload_hash: str
    signature: str
    key_id: str
    algorithm: str = "hmac-sha256"


@dataclass(frozen=True)
class AuditAnchor:
    anchor_type: str
    payload_hash: str
    signature: str
    key_id: str


@dataclass(frozen=True)
class OperatorKeyPolicy:
    key_id: str
    allowed_actions: tuple[str, ...]
    emergency_freeze: bool = False
    hardware_backed: bool = False

    def permits(self, action: str) -> bool:
        return not self.emergency_freeze and str(action) in set(self.allowed_actions)


class CryptographicGovernance:
    """Off-chain signed artifact baseline with optional Ed25519 operator signing."""

    def sign(self, artifact_type: str, payload: Mapping[str, object], *, secret: str, key_id: str) -> SignedArtifact:
        payload_hash = _payload_hash(payload)
        signature = hmac.new(str(secret).encode("utf-8"), payload_hash.encode("ascii"), hashlib.sha256).hexdigest()
        return SignedArtifact(str(artifact_type), payload_hash, signature, str(key_id))

    def verify(self, artifact: SignedArtifact, payload: Mapping[str, object], *, secret: str) -> bool:
        if artifact.payload_hash != _payload_hash(payload):
            return False
        expected = hmac.new(str(secret).encode("utf-8"), artifact.payload_hash.encode("ascii"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(artifact.signature, expected)

    def sign_ed25519(
        self,
        artifact_type: str,
        payload: Mapping[str, object],
        *,
        private_key_pem: str | bytes,
        key_id: str,
    ) -> SignedArtifact:
        payload_hash = _payload_hash(payload)
        signature = _ed25519_sign(private_key_pem, payload_hash.encode("ascii"))
        return SignedArtifact(str(artifact_type), payload_hash, signature, str(key_id), "ed25519")

    def verify_ed25519(
        self,
        artifact: SignedArtifact,
        payload: Mapping[str, object],
        *,
        public_key_pem: str | bytes,
    ) -> bool:
        if artifact.algorithm != "ed25519" or artifact.payload_hash != _payload_hash(payload):
            return False
        return _ed25519_verify(public_key_pem, artifact.signature, artifact.payload_hash.encode("ascii"))

    def anchor(self, artifact: SignedArtifact) -> AuditAnchor:
        return AuditAnchor(artifact.artifact_type, artifact.payload_hash, artifact.signature, artifact.key_id)


def _payload_hash(payload: Mapping[str, object]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ed25519_sign(private_key_pem: str | bytes, message: bytes) -> str:
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except ImportError as exc:  # pragma: no cover - depends on deployment image
        raise RuntimeError("cryptography_required_for_ed25519_signing") from exc

    raw_key = private_key_pem.encode("utf-8") if isinstance(private_key_pem, str) else private_key_pem
    private_key = load_pem_private_key(raw_key, password=None)
    signature = private_key.sign(message)
    return base64.b64encode(signature).decode("ascii")


def _ed25519_verify(public_key_pem: str | bytes, signature: str, message: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError as exc:  # pragma: no cover - depends on deployment image
        raise RuntimeError("cryptography_required_for_ed25519_verification") from exc

    raw_key = public_key_pem.encode("utf-8") if isinstance(public_key_pem, str) else public_key_pem
    public_key = load_pem_public_key(raw_key)
    try:
        public_key.verify(base64.b64decode(signature.encode("ascii")), message)
    except (InvalidSignature, ValueError):
        return False
    return True
