def test_package_exports_required_api():
    import prospective_harness as ph

    assert callable(ph.register_prediction)
    assert callable(ph.record_outcome)
    assert callable(ph.calibration_report)
    assert ph.ImmutablePredictionError.__name__ == "ImmutablePredictionError"
    assert ph.TemporalOrderingError.__name__ == "TemporalOrderingError"


def test_package_exports_attestation_api():
    import prospective_harness as ph

    assert callable(ph.AttestationStore)
    assert ph.ChainVerificationError.__name__ == "ChainVerificationError"
    assert ph.WriteLatencyExceededError.__name__ == "WriteLatencyExceededError"

