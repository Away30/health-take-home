from prospective_harness.hashing import canonical_json, content_hash


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})


def test_content_hash_is_sha256_hex_and_stable():
    payload = {"prediction": {"probability": 0.7}, "model_id": "m1"}

    digest = content_hash(payload)

    assert len(digest) == 64
    assert digest == content_hash({"model_id": "m1", "prediction": {"probability": 0.7}})
    assert digest != content_hash({"model_id": "m1", "prediction": {"probability": 0.8}})
