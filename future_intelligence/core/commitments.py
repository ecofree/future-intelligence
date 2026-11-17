"""FI-JSON-v1 and FI-MERKLE-v1. Integrity proofs are NOT timestamp proofs."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

_HEX = re.compile(r"^[0-9a-f]{64}$")


def _check_json(value: Any) -> None:
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is list:
        for item in value:
            _check_json(item)
        return
    if type(value) is dict:
        if any(type(k) is not str for k in value):
            raise ValueError("JSON object keys must be strings")
        for item in value.values():
            _check_json(item)
        return
    raise ValueError("FI-JSON-v1 rejects floats and non-JSON types; use scaled integers")


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    if type(payload) is not dict:
        raise ValueError("the root must be a JSON object")
    _check_json(payload)
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, UnicodeError) as exc:
        raise ValueError("payload is not valid UTF-8 JSON") from exc


def new_nonce() -> str:
    return secrets.token_hex(32)


def _digest_bytes(digest: str) -> bytes:
    if not isinstance(digest, str) or _HEX.fullmatch(digest) is None:
        raise ValueError("digest must be lowercase 64-character SHA-256 hex")
    return bytes.fromhex(digest)


def record_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(b"FI:record:v1\0" + canonical_bytes(payload)).hexdigest()


def make_commitment(payload: dict[str, Any], *, nonce: str | None = None) -> tuple[dict, str]:
    """Return a committed envelope and its hash. Fixed nonces are for tests only."""
    nonce = new_nonce() if nonce is None else nonce
    _digest_bytes(nonce)
    # Copy through the canonical encoding so later caller mutation is isolated.
    snapshot = json.loads(canonical_bytes(payload))
    envelope = {"schema": "FI:commitment:v1", "nonce": nonce, "payload": snapshot}
    return envelope, record_hash(envelope)


def _leaf(digest: str) -> bytes:
    return hashlib.sha256(b"\x00" + _digest_bytes(digest)).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


@dataclass(frozen=True)
class ProofStep:
    sibling_hash: str
    sibling_on_left: bool


@dataclass(frozen=True)
class InclusionProof:
    leaf_index: int
    leaf_count: int
    steps: tuple[ProofStep, ...]


def merkle_root_and_proofs(record_digests: list[str]) -> tuple[str, list[InclusionProof]]:
    if not record_digests:
        raise ValueError("empty batches are not allowed")
    layers = [[_leaf(h) for h in record_digests]]
    while len(layers[-1]) > 1:
        layer = layers[-1]
        padded = layer + [layer[-1]] if len(layer) % 2 else layer
        layers.append([_node(padded[i], padded[i + 1]) for i in range(0, len(padded), 2)])
    proofs = []
    for start in range(len(record_digests)):
        position = start
        steps = []
        for layer in layers[:-1]:
            sibling = position ^ 1
            if sibling >= len(layer):
                sibling = position
            steps.append(ProofStep(layer[sibling].hex(), position % 2 == 1))
            position //= 2
        proofs.append(InclusionProof(start, len(record_digests), tuple(steps)))
    return layers[-1][0].hex(), proofs


def verify_inclusion(record_digest: str, proof: InclusionProof, root: str) -> bool:
    try:
        _digest_bytes(root)
        if (type(proof.leaf_count) is not int or type(proof.leaf_index) is not int
                or proof.leaf_count < 1 or not 0 <= proof.leaf_index < proof.leaf_count):
            return False
        value, pos, width = _leaf(record_digest), proof.leaf_index, proof.leaf_count
        for step in proof.steps:
            if width <= 1 or type(step.sibling_on_left) is not bool:
                return False
            if step.sibling_on_left != (pos % 2 == 1):
                return False
            sibling = _digest_bytes(step.sibling_hash)
            if pos % 2 == 0 and pos + 1 == width and not hmac.compare_digest(value, sibling):
                return False  # Odd-width duplication must repeat the current node.
            value = _node(sibling, value) if step.sibling_on_left else _node(value, sibling)
            pos, width = pos // 2, (width + 1) // 2
        return width == 1 and hmac.compare_digest(value.hex(), root)
    except (ValueError, TypeError, AttributeError):
        return False
