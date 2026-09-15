"""Manual, secret-safe source audit for direct venue REST read bypasses."""

from __future__ import annotations

import argparse
import ast
import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

_SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".worktrees",
    "__pycache__",
    "build",
    "dist",
    "docs",
    "exports",
    "fixtures",
    "node_modules",
    "site-packages",
    "tests",
    "worktrees",
}
_SKIP_PREFIXES = (".pytest-tmp",)
_VENUE_HOSTS = {
    "kalshi": ("api.elections.kalshi.com", "external-api.kalshi.com"),
    "pmus": ("api.polymarket.us", "gateway.polymarket.us"),
}


@dataclass(frozen=True, slots=True)
class Finding:
    root: Path
    path: Path
    line: int
    venue: str
    reason: str


def audit_roots(
    roots: Iterable[Path], *, excludes: Iterable[Path] = ()
) -> list[Finding]:
    """Find possible direct REST reads without inspecting credentials or state."""
    resolved_excludes = tuple(path.resolve() for path in excludes)
    findings: list[Finding] = []
    for raw_root in roots:
        root = raw_root.resolve()
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            resolved = path.resolve()
            if _is_excluded(resolved, resolved_excludes) or _skip_path(resolved, root):
                continue
            findings.extend(_audit_python_file(root, resolved))
    return sorted(
        findings, key=lambda item: (str(item.path).lower(), item.line, item.venue)
    )


def _is_excluded(path: Path, excludes: tuple[Path, ...]) -> bool:
    return any(path == excluded or excluded in path.parents for excluded in excludes)


def _skip_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(
        part.lower() in _SKIP_PARTS
        or part.lower().startswith(_SKIP_PREFIXES)
        for part in relative.parts[:-1]
    )


def _audit_python_file(root: Path, path: Path) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    names = _string_names(tree)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    http_clients = _http_client_names(tree, parents)
    found: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _network_get_target(node, names, http_clients, parents)
        if target is None:
            continue
        value = target.lower()
        for venue, hosts in _VENUE_HOSTS.items():
            if any(host in value for host in hosts):
                key = (node.lineno, venue)
                if key not in seen:
                    seen.add(key)
                    found.append(
                        Finding(
                            root=root,
                            path=path,
                            line=node.lineno,
                            venue=venue,
                            reason="direct venue host in a REST GET call",
                        )
                    )
    return found


def _string_names(tree: ast.AST) -> dict[str, str]:
    names: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is None:
                continue
            value = _string_fragment(node.value, names)
            if not value:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names[target.id] = value
                    changed = True
    return names


def _string_fragment(node: ast.AST, names: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id, "")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _string_fragment(node.left, names) + _string_fragment(node.right, names)
    if isinstance(node, ast.JoinedStr):
        return "".join(_string_fragment(value, names) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _string_fragment(node.value, names)
    return ""


def _scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return current
    return current


def _http_client_names(
    tree: ast.AST, parents: dict[ast.AST, ast.AST]
) -> set[tuple[ast.AST, str]]:
    clients: set[tuple[ast.AST, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if _is_http_client_constructor(item.context_expr) and isinstance(
                    item.optional_vars, ast.Name
                ):
                    clients.add((_scope(node, parents), item.optional_vars.id))
        if not isinstance(node, ast.Assign) or not _is_http_client_constructor(
            node.value
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                clients.add((_scope(node, parents), target.id))
    return clients


def _is_http_client_constructor(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    return (
        isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"httpx", "requests"}
        and node.func.attr in {"Client", "AsyncClient", "Session"}
    )


def _owner_is_direct_http(
    node: ast.AST,
    call: ast.Call,
    http_clients: set[tuple[ast.AST, str]],
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if isinstance(node, ast.Name):
        return (
            node.id in {"httpx", "requests"}
            or (_scope(call, parents), node.id) in http_clients
        )
    if isinstance(node, ast.Attribute):
        return node.attr.lower() in {"session", "http", "http_client"}
    return False


def _network_get_target(
    node: ast.Call,
    names: dict[str, str],
    http_clients: set[tuple[ast.AST, str]],
    parents: dict[ast.AST, ast.AST],
) -> str | None:
    if isinstance(node.func, ast.Name) and node.func.id == "urlopen" and node.args:
        return _string_fragment(node.args[0], names)
    if not isinstance(node.func, ast.Attribute):
        return None
    if (
        node.func.attr == "get"
        and node.args
        and _owner_is_direct_http(node.func.value, node, http_clients, parents)
    ):
        return _string_fragment(node.args[0], names)
    if (
        node.func.attr != "request"
        or len(node.args) < 2
        or not _owner_is_direct_http(node.func.value, node, http_clients, parents)
    ):
        return None
    method = _string_fragment(node.args[0], names).upper()
    if method and method != "GET":
        return None
    return _string_fragment(node.args[1], names)


def format_report(findings: Sequence[Finding]) -> str:
    if not findings:
        return "No possible direct Kalshi or Polymarket US REST bypasses found."
    noun = "bypass" if len(findings) == 1 else "bypasses"
    lines = [f"{len(findings)} possible REST {noun} found:"]
    lines.extend(
        f"- {item.path}:{item.line} [{item.venue}] {item.reason}" for item in findings
    )
    lines.append(
        "Review each finding; this conservative source check proves presence, "
        "not runtime use."
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Manually scan source roots for direct Kalshi/Polymarket US REST reads."
        )
    )
    parser.add_argument("--root", action="append", required=True, type=Path)
    parser.add_argument("--exclude", action="append", default=[], type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    findings = audit_roots(args.root, excludes=args.exclude)
    if args.as_json:
        print(
            json.dumps(
                [
                    {
                        **asdict(item),
                        "root": str(item.root),
                        "path": str(item.path),
                    }
                    for item in findings
                ],
                indent=2,
            )
        )
    else:
        print(format_report(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
