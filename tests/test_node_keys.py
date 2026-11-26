"""Tests for the signing-only trusted node-key interface."""

import pytest

from ubc_node.node_keys import NodeSignature


def test_node_signature_accepts_ed25519_output() -> None:
    value = NodeSignature("node-1", "ed25519", b"s" * 64)
    assert value.key_id == "node-1"


@pytest.mark.parametrize(
    ("key_id", "algorithm", "signature", "message"),
    [
        ("", "ed25519", b"s" * 64, "key_id"),
        ("node", "rsa", b"s" * 64, "ed25519"),
        ("node", "ed25519", b"short", "64 bytes"),
    ],
)
def test_node_signature_rejects_invalid_values(
    key_id: str, algorithm: str, signature: bytes, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        NodeSignature(key_id, algorithm, signature)
