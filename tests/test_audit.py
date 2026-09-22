from audit import compute_payload_hash, compute_chain_hash, GENESIS_HASH


def test_same_payload_produces_same_hash():
    payload = {"vin": "ABC123", "kwh_consumed": 0.5}
    assert compute_payload_hash(payload) == compute_payload_hash(payload)


def test_different_payload_produces_different_hash():
    payload_a = {"vin": "ABC123", "kwh_consumed": 0.5}
    payload_b = {"vin": "ABC123", "kwh_consumed": 0.51}  # tiny change
    assert compute_payload_hash(payload_a) != compute_payload_hash(payload_b)


def test_hash_is_order_independent():
    # Dict key order shouldn't matter — same logical data, same hash
    payload_a = {"vin": "ABC123", "kwh_consumed": 0.5}
    payload_b = {"kwh_consumed": 0.5, "vin": "ABC123"}
    assert compute_payload_hash(payload_a) == compute_payload_hash(payload_b)


def test_chain_hash_links_to_previous():
    payload_hash = compute_payload_hash({"vin": "ABC123"})
    chain_1 = compute_chain_hash(payload_hash, GENESIS_HASH)
    chain_2 = compute_chain_hash(payload_hash, "some-other-previous-hash")

    # Same payload hash, but different previous chain hash => different result.
    # This is what makes the chain tamper-evident.
    assert chain_1 != chain_2


def test_tampering_with_past_record_changes_its_hash():
    original = {"vin": "ABC123", "kwh_consumed": 0.5}
    tampered = {"vin": "ABC123", "kwh_consumed": 0.05}  # someone edited this later

    original_hash = compute_payload_hash(original)
    tampered_hash = compute_payload_hash(tampered)

    # If a verifier recomputes the hash from current (tampered) data,
    # it won't match what was originally stored — tampering is detectable.
    assert original_hash != tampered_hash
