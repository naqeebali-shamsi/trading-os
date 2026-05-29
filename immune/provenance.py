#!/usr/bin/env python3
"""
immune/provenance.py -- signed immune.pass provenance proofs
------------------------------------------------------------
Muscle must not trust a caller-controlled ``mode_check`` flag by itself.  This
module creates and verifies a compact HMAC proof over the safety-critical order
fields that immune approved.  The proof is embedded in the passed intent and can
be revalidated by downstream routers before any IPC command is written.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = ROOT / "immune" / ".provenance_key"
PROOF_VERSION = "immune-pass-v1"
DEFAULT_MAX_AGE_SEC = 10 * 60

# Include fields whose post-immune mutation would materially change execution.
SIGNED_INTENT_FIELDS = (
    "order_id",
    "symbol",
    "side",
    "qty",
    "type",
    "price",
    "sl",
    "tp",
)


def _load_or_create_key() -> bytes:
    env_key = os.getenv("IMMUNE_PROVENANCE_KEY")
    if env_key:
        return env_key.encode("utf-8")

    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(32))
        try:
            KEY_FILE.chmod(0o600)
        except OSError:
            pass
    return KEY_FILE.read_text().strip().encode("utf-8")


def _canonical_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def canonical_intent(intent: Dict[str, Any], fields: Iterable[str] = SIGNED_INTENT_FIELDS) -> Dict[str, Any]:
    """Return only signed order fields, preserving explicit nulls when present."""
    return {field: intent.get(field) for field in fields if field in intent}


def intent_digest(intent: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(canonical_intent(intent)).encode("utf-8")).hexdigest()


def _signature(proof_body: Dict[str, Any], key: Optional[bytes] = None) -> str:
    key = key or _load_or_create_key()
    return hmac.new(key, _canonical_json(proof_body).encode("utf-8"), hashlib.sha256).hexdigest()


def make_proof(intent: Dict[str, Any], source_event: Optional[Dict[str, Any]] = None, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Create a provenance proof for an immune-approved order intent."""
    proof_body = {
        "version": PROOF_VERSION,
        "issued_at": float(now if now is not None else time.time()),
        "intent_digest": intent_digest(intent),
        "source_topic": (source_event or {}).get("topic", "muscle.order.intent"),
        "source_seq": (source_event or {}).get("seq"),
        "issuer": "immune",
    }
    return {**proof_body, "signature": _signature(proof_body)}


def attach_proof(intent: Dict[str, Any], source_event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a copy of intent with mode_check and immune_proof set."""
    approved = dict(intent)
    approved["mode_check"] = True
    approved["immune_proof"] = make_proof(approved, source_event)
    return approved


def verify_proof(intent: Dict[str, Any], *, max_age_sec: float = DEFAULT_MAX_AGE_SEC, now: Optional[float] = None) -> Tuple[bool, str]:
    """Validate an immune proof embedded in an intent.

    Returns (ok, reason).  Reasons are stable enough for rejection telemetry and
    tests, but intentionally do not expose the HMAC key or expected signature.
    """
    proof = intent.get("immune_proof")
    if not isinstance(proof, dict):
        return False, "missing_immune_proof"

    signature = proof.get("signature")
    if not isinstance(signature, str) or not signature:
        return False, "missing_immune_signature"

    proof_body = {k: v for k, v in proof.items() if k != "signature"}
    if proof_body.get("version") != PROOF_VERSION:
        return False, "unsupported_immune_proof_version"
    if proof_body.get("issuer") != "immune":
        return False, "invalid_immune_proof_issuer"

    issued_at = proof_body.get("issued_at")
    if not isinstance(issued_at, (int, float)):
        return False, "invalid_immune_proof_timestamp"
    age = float(now if now is not None else time.time()) - float(issued_at)
    if age < -30:
        return False, "immune_proof_from_future"
    if age > max_age_sec:
        return False, "stale_immune_proof"

    if proof_body.get("intent_digest") != intent_digest(intent):
        return False, "immune_proof_intent_mismatch"

    expected = _signature(proof_body)
    if not hmac.compare_digest(signature, expected):
        return False, "invalid_immune_signature"

    return True, "ok"
