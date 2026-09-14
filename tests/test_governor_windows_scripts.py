"""Contract tests for the hidden Governor launcher and the desktop shortcut.

These tests read the shipped Windows scripts as text. They assert the operating
rules that keep Governor loopback-only, single-instance, console-free, and free
of credential material. They never execute a script and never touch the
existing ThrottleDeck Broker scheduled task.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "scripts"
START_PS1 = SCRIPTS / "start-governor-hidden.ps1"
START_VBS = SCRIPTS / "start-governor-hidden.vbs"
INSTALL_PS1 = SCRIPTS / "install-governor-shortcut.ps1"

GOVERNOR_PORT = "8778"
BROKER_PORT = "8777"

# Cmdlets that create or stop scheduled tasks and processes. They are assembled
# from two pieces so this test file never contains a runnable form of them.
FORBIDDEN_CMD_PAIRS = (
    ("Register", "-ScheduledTask"),
    ("Unregister", "-ScheduledTask"),
    ("Start", "-ScheduledTask"),
    ("Stop", "-ScheduledTask"),
    ("Stop", "-Process"),
    ("Restart", "-Computer"),
    ("sch", "tasks"),
    ("Is", "InRole"),
    ("Run", "As"),
)


def forbidden_commands() -> tuple[str, ...]:
    return tuple(verb + noun for verb, noun in FORBIDDEN_CMD_PAIRS)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def executable_lines(script: str) -> list[str]:
    """Script lines that actually do something, ignoring comments."""
    return [line for line in script.splitlines() if not line.strip().startswith("#")]


@pytest.fixture(scope="module")
def start_ps1() -> str:
    return read(START_PS1)


@pytest.fixture(scope="module")
def start_vbs() -> str:
    return read(START_VBS)


@pytest.fixture(scope="module")
def install_ps1() -> str:
    return read(INSTALL_PS1)


def test_all_three_scripts_exist_and_are_not_empty() -> None:
    for path in (START_PS1, START_VBS, INSTALL_PS1):
        assert path.is_file(), f"missing {path.name}"
        assert read(path).strip(), f"{path.name} is empty"


def test_launcher_stays_on_loopback(start_ps1: str) -> None:
    assert "http://127.0.0.1:8778" in start_ps1
    for forbidden in ("0.0.0.0", "http://+", "http://*", "localhost:"):
        assert forbidden not in start_ps1, f"launcher must not use {forbidden}"


def test_launcher_starts_governor_and_not_the_broker(
    start_ps1: str, start_vbs: str
) -> None:
    assert GOVERNOR_PORT in start_ps1
    assert "-m', 'governor'" in start_ps1
    for forbidden in ("venue_broker", "venue-broker", f"LocalPort {BROKER_PORT}"):
        assert forbidden not in start_ps1, f"launcher must not touch {forbidden}"
        assert forbidden not in start_vbs


def test_launcher_never_shows_a_console_window(start_ps1: str, start_vbs: str) -> None:
    assert "-WindowStyle Hidden" in start_ps1
    assert start_ps1.count("-WindowStyle") == start_ps1.count("-WindowStyle Hidden")
    assert "shell.Run command, 0, False" in start_vbs
    for script in (start_ps1, start_vbs):
        for forbidden in (
            "cmd.exe",
            "conhost",
            "-WindowStyle Normal",
            "-WindowStyle Minimized",
            "-WindowStyle Maximized",
        ):
            assert forbidden not in script, f"console host leak: {forbidden}"


def test_vbs_wrapper_starts_the_launcher_and_returns_immediately(
    start_vbs: str,
) -> None:
    assert "start-governor-hidden.ps1" in start_vbs
    assert "powershell.exe" in start_vbs
    assert "-NoProfile" in start_vbs
    assert "-NonInteractive" in start_vbs
    assert "-ExecutionPolicy Bypass" in start_vbs
    assert "WScript.ScriptFullName" in start_vbs


def test_launcher_starts_governor_only_when_health_is_absent(start_ps1: str) -> None:
    guard = "if (-not (Test-GovernorHealthy))"
    assert guard in start_ps1
    assert start_ps1.index("/health") < start_ps1.index(guard)
    assert start_ps1.index(guard) < start_ps1.index("Start-Process -FilePath $python")


def test_launcher_waits_with_a_bounded_timeout(start_ps1: str) -> None:
    assert "$healthTimeoutSeconds = 25" in start_ps1
    assert "AddSeconds($healthTimeoutSeconds)" in start_ps1
    assert "Start-Sleep -Milliseconds 500" in start_ps1
    assert "exit 1" in start_ps1, "a health timeout must fail loudly"


def test_launcher_opens_the_installed_edge_in_app_mode(start_ps1: str) -> None:
    assert "'-m', 'governor', '--open'" in start_ps1
    forbidden_fetchers = (
        "Start-BitsTransfer",
        "Invoke-WebRequest -OutFile",
        "npm",
        "curl",
    )
    for forbidden in forbidden_fetchers:
        assert forbidden not in start_ps1, f"must not fetch a browser: {forbidden}"


def test_launcher_uses_a_one_use_launch_nonce(start_ps1: str) -> None:
    assert "'-m', 'governor', '--open'" in start_ps1
    assert "Invoke-RestMethod" not in start_ps1


def test_launcher_keeps_the_control_key_out_of_arguments_and_logs(
    start_ps1: str,
) -> None:
    assert "$keyPath" not in start_ps1
    assert "control.key" not in start_ps1
    for line in executable_lines(start_ps1):
        for sink in (
            "Write-Host",
            "Write-Output",
            "Write-Verbose",
            "Out-File",
            "Add-Content",
        ):
            if sink in line:
                assert "$key" not in line, f"secret leak: {line}"
                assert "$nonce" not in line.lower()


def test_launcher_redirects_service_logs_outside_the_window(start_ps1: str) -> None:
    assert "-RedirectStandardOutput" in start_ps1
    assert "-RedirectStandardError" in start_ps1
    assert "$logDir = Join-Path $root 'logs'" in start_ps1
    assert "'governor.out.log'" in start_ps1
    assert "'governor.err.log'" in start_ps1
    assert "New-Item -ItemType Directory -Path $logDir" in start_ps1


def test_launcher_exits_cleanly_when_governor_is_already_up(start_ps1: str) -> None:
    assert "$baseUrl = 'http://127.0.0.1:8778'" in start_ps1
    assert "exit 0" in start_ps1
    assert "Test-GovernorHealthy" in start_ps1


def test_launcher_contract_matches_the_python_service(start_ps1: str) -> None:
    assert '$healthUrl = "$baseUrl/health"' in start_ps1
    assert "/internal/v1/launch-nonce" not in start_ps1
    assert "X-Governor-Key" not in start_ps1


def test_launcher_registers_no_task_and_stops_nothing(
    start_ps1: str, start_vbs: str
) -> None:
    for script in (start_ps1, start_vbs):
        for command in forbidden_commands():
            assert command not in script, f"launcher must not use {command}"
    for line in executable_lines(start_ps1):
        assert "ThrottleDeck Broker" not in line, (
            "the broker task name may only be explained"
        )
        assert "-TaskName" not in line


def test_shortcut_installer_creates_one_exact_shortcut(install_ps1: str) -> None:
    assert "CreateShortcut(" in install_ps1
    assert install_ps1.count("CreateShortcut(") == 1
    assert "$shortcutName = 'ThrottleDeck'" in install_ps1
    assert "[Environment]::GetFolderPath('Desktop')" in install_ps1
    assert ".Save()" in install_ps1


def test_shortcut_runs_the_hidden_launcher_without_a_console(install_ps1: str) -> None:
    assert "wscript.exe" in install_ps1
    assert "start-governor-hidden.vbs" in install_ps1
    assert "$shortcut.WindowStyle = 7" in install_ps1
    assert "$shortcut.Arguments = '//B" in install_ps1
    for forbidden in ("powershell.exe", "cmd.exe", "conhost"):
        assert forbidden not in install_ps1, f"shortcut must not target {forbidden}"


def test_shortcut_installer_needs_no_elevation_and_no_task(install_ps1: str) -> None:
    for command in forbidden_commands():
        assert command not in install_ps1, f"installer must not use {command}"
    for line in executable_lines(install_ps1):
        assert "ThrottleDeck Broker" not in line
        assert "-TaskName" not in line


def test_scripts_carry_no_credential_material(
    start_ps1: str, start_vbs: str, install_ps1: str
) -> None:
    for script in (start_ps1, start_vbs, install_ps1):
        lowered = script.lower()
        for forbidden in (
            "authorization",
            "api_key",
            "apikey",
            "api-key",
            "bearer ",
            "password",
            "secret",
        ):
            assert forbidden not in lowered, f"credential text found: {forbidden}"
