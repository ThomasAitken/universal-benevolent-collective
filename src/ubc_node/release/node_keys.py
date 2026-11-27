"""Narrow trusted-core interface for a non-exportable node signing key."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class NodeSignature:
    """Detached signature returned by a trusted node-key provider."""

    key_id: str
    algorithm: str
    signature: bytes

    def __post_init__(self) -> None:
        if not self.key_id.strip():
            raise ValueError("key_id must not be empty")
        if self.algorithm != "ed25519":
            raise ValueError("only ed25519 node signatures are supported")
        if len(self.signature) != 64:
            raise ValueError("an ed25519 signature must contain 64 bytes")


class NodeKey(Protocol):
    """Signing-only authority; private key material is never returned."""

    @property
    def key_id(self) -> str:
        """Return the public identifier for this node key."""
        ...

    async def sign(self, payload: bytes) -> NodeSignature:
        """Sign a bounded protocol payload without exporting the private key."""
        ...
