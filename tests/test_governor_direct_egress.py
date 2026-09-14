from governor.direct_egress import classify_connections


def test_classifies_only_known_venue_connections_and_excludes_the_broker() -> None:
    result = classify_connections(
        [
            {
                "remote_address": "203.0.113.10",
                "process_name": "python",
                "pid": 101,
                "observations": 4,
                "is_broker": False,
            },
            {
                "remote_address": "198.51.100.20",
                "process_name": "python",
                "pid": 202,
                "observations": 3,
                "is_broker": True,
            },
            {
                "remote_address": "192.0.2.9",
                "process_name": "msedge",
                "pid": 303,
                "observations": 9,
                "is_broker": False,
            },
        ],
        {
            "polymarket-us": {"203.0.113.10"},
            "kalshi": {"198.51.100.20"},
        },
        sampled_for_ms=2_000,
    )

    assert result["status"] == "findings"
    assert result["findings"] == [
        {
            "venue": "polymarket-us",
            "process_name": "python",
            "pid": 101,
            "observations": 4,
        }
    ]
    encoded = repr(result).lower()
    assert "command_line" not in encoded
    assert "header" not in encoded
    assert "api_key" not in encoded


def test_clear_result_states_that_sampling_is_not_proof() -> None:
    result = classify_connections(
        [], {"polymarket-us": {"203.0.113.10"}}, sampled_for_ms=2_000
    )
    assert result["status"] == "clear"
    assert "does not prove" in result["caveat"].lower()
