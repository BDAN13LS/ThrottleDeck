from pathlib import Path

from venue_broker.adoption_audit import Finding, audit_roots, format_report


def _write(root: Path, relative: str, source: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_audit_finds_direct_venue_get_with_exact_line(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "app/client.py",
        'import requests\nBASE = "https://api.elections.kalshi.com/trade-api/v2"\n'
        'def read():\n    return requests.get(f"{BASE}/markets")\n',
    )

    assert audit_roots([tmp_path]) == [
        Finding(
            root=tmp_path,
            path=path,
            line=4,
            venue="kalshi",
            reason="direct venue host in a REST GET call",
        )
    ]


def test_audit_ignores_local_broker_websocket_and_post_only(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "safe/broker.py",
        'import requests\nrequests.get("http://127.0.0.1:8777/kalshi/markets")\n',
    )
    _write(
        tmp_path,
        "safe/socket.py",
        'URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"\n',
    )
    _write(
        tmp_path,
        "safe/write.py",
        'import requests\nURL = "https://api.polymarket.us/v1/orders"\nrequests.post(URL)\n',
    )

    assert audit_roots([tmp_path]) == []


def test_report_is_actionable_without_file_contents(tmp_path: Path) -> None:
    path = tmp_path / "client.py"
    report = format_report(
        [Finding(tmp_path, path, 9, "kalshi", "direct venue host in a REST GET call")]
    )

    assert f"{path}:9" in report
    assert "kalshi" in report
    assert "1 possible REST bypass" in report
