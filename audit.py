import hashlib
import json

GENESIS_HASH = "0" * 64  # the "previous hash" for the very first record in the chain


def compute_payload_hash(payload: dict) -> str:
    """
    SHA-256 hash of this reading's own raw data.
    Uses sort_keys + default=str so the hash is deterministic regardless of
    dict ordering or datetime objects.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_chain_hash(payload_hash: str, previous_chain_hash: str) -> str:
    """
    Links this record to the one before it. Changing ANY past record's
    payload_hash changes this value for every record after it — that's
    what makes tampering detectable.
    """
    combined = f"{previous_chain_hash}:{payload_hash}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
