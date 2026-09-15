from venue_broker.version import resolve_commit_sha


def test_explicit_build_sha_wins_without_running_git(monkeypatch) -> None:
    monkeypatch.setenv(
        "VENUE_BROKER_COMMIT_SHA", "ABCDEF1234567890ABCDEF1234567890ABCDEF12"
    )

    assert resolve_commit_sha() == "abcdef1234567890abcdef1234567890abcdef12"


def test_invalid_explicit_build_sha_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("VENUE_BROKER_COMMIT_SHA", "not-a-commit")

    assert resolve_commit_sha() == "unknown"
