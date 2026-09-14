"""Remove each review fix in a disposable copy, then restore and retest it.

Run with: python scripts/prove_review_regressions.py
No network, service, scheduled task, credential files, or real data are used.
The current working tree is copied so uncommitted fixes can also be proved.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "9eb172c"
REGRESSIONS = "tests/test_review_regressions.py"


def function_span(source: str, qualified_name: str) -> tuple[int, int]:
    node = ast.parse(source)
    for part in qualified_name.split("."):
        node = next(
            child for child in node.body if getattr(child, "name", None) == part
        )
    return node.lineno - 1, node.end_lineno


def restore_function(source: str, old: str, name: str) -> str:
    start, end = function_span(source, name)
    old_start, old_end = function_span(old, name)
    lines = source.splitlines(keepends=True)
    lines[start:end] = old.splitlines(keepends=True)[old_start:old_end]
    return "".join(lines)


def replacement(before: str, after: str) -> Callable[[str], str]:
    def apply(source: str) -> str:
        if source.count(before) != 1:
            raise AssertionError(f"Mutation anchor must occur exactly once: {before!r}")
        return source.replace(before, after, 1)

    return apply


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str
    selector: str
    change: Callable[[str], str]
    tests: str = REGRESSIONS


def mutations() -> list[Mutation]:
    originals = {}
    for file in ("broker.py", "limiter.py", "config.py", "app.py"):
        originals[file] = subprocess.run(
            ["git", "show", f"{BASELINE}:venue_broker/{file}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    def old_function(file: str, name: str) -> Callable[[str], str]:
        return lambda source: restore_function(source, originals[file], name)

    return [
        Mutation(
            "credential identity",
            "broker.py",
            "different_credentials",
            replacement(
                "self._credential_scope(normalized_headers)", 'f"caller:{caller}"'
            ),
        ),
        Mutation(
            "credential secrecy",
            "broker.py",
            "different_credentials or matching_credentials",
            replacement(
                "hmac.new(self._fingerprint_key, material, hashlib.sha256).hexdigest()",
                "material.decode()",
            ),
        ),
        Mutation(
            "rolling ceiling",
            "limiter.py",
            "rolling",
            old_function("limiter.py", "TokenBucket.acquire"),
        ),
        Mutation(
            "authenticated host",
            "broker.py",
            "documented_surface",
            old_function("broker.py", "upstream_url"),
        ),
        Mutation(
            "nonblocking metrics",
            "limiter.py",
            "monitoring",
            old_function("limiter.py", "TokenBucket.available"),
        ),
        Mutation(
            "timeout labeling (already fixed)",
            "broker.py",
            "upstream_timeout",
            replacement(
                "except (httpx.TimeoutException, TimeoutError) as error:",
                "except () as error:",
            ),
            "tests/test_failures.py",
        ),
        Mutation(
            "leader cancellation (already fixed)",
            "broker.py",
            "cancelled_leader",
            replacement("return await asyncio.shield(task)", "return await task"),
        ),
        Mutation(
            "price history TTL",
            "config.py",
            "price_history",
            replacement('r"/v1/price-history"', 'r"/disabled-price-history"'),
        ),
        Mutation(
            "Retry-After",
            "broker.py",
            "retry_after",
            old_function("broker.py", "parse_retry_after"),
        ),
        Mutation(
            "encoded non-ASCII paths",
            "app.py",
            "percent_encoded_non_ascii",
            old_function("app.py", "BrokerApp.__call__"),
        ),
        Mutation(
            "literal queue cost",
            "config.py",
            "literal_queue_positions",
            replacement(
                'r"/portfolio/orders/queue_positions"', 'r"/disabled-queue-positions"'
            ),
        ),
        Mutation(
            "batch item accounting",
            "config.py",
            "batch_cost or entire_batch or invalid_batches",
            old_function("config.py", "kalshi_token_cost"),
        ),
        Mutation(
            "oversized admission rejection",
            "broker.py",
            "entire_batch",
            replacement("if cost > config.capacity:", "if False:"),
        ),
        Mutation(
            "perps isolation",
            "broker.py",
            "perps",
            replacement(
                'if venue == "kalshi" and is_kalshi_perps_path(path):', "if False:"
            ),
        ),
        Mutation(
            "per-request retry timing",
            "broker.py",
            "retry_timing_separates",
            replacement(
                'timing["retry_sleep_ms"] += detail["retry_sleep_ms"]',
                'timing["retry_sleep_ms"] += 0',
            ),
            "tests/test_request_timing.py",
        ),
        Mutation(
            "stopgap never retries",
            "broker.py",
            "documented_stopgap",
            replacement(
                "if is_polymarket_latency_stopgap(venue, response):", "if False:"
            ),
            "tests/test_request_timing.py",
        ),
        Mutation(
            "HTTP trace wiring",
            "broker.py",
            "http_trace_is_wired",
            replacement('extensions={"trace": trace}', "extensions={}"),
            "tests/test_request_timing.py",
        ),
        Mutation(
            "cache timing isolation",
            "broker.py",
            "cache_hit_does_not_replay",
            replacement(
                'replace(entry.response, timing=empty_timing("cache"))',
                "entry.response",
            ),
            "tests/test_request_timing.py",
        ),
        Mutation(
            "HTTP client cookie isolation",
            "broker.py",
            "upstream_cookies",
            old_function("broker.py", "HttpxFetcher.__init__"),
        ),
        Mutation(
            "write batches remain refused",
            "app.py",
            "write_batches",
            replacement('if method != "GET":', "if False:"),
        ),
    ]


def run_tests(directory: Path, mutation: Mutation) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("VENUE_BROKER_")
    }
    environment["PYTHONPATH"] = str(directory)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            mutation.tests,
            "-k",
            mutation.selector,
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def main() -> None:
    cases = mutations()
    with tempfile.TemporaryDirectory(prefix=".review-proof-", dir=ROOT) as temporary:
        directory = Path(temporary).resolve()
        # Validate the exact recursive-cleanup target before using the context.
        assert directory.parent == ROOT and directory.name.startswith(".review-proof-")
        for name in ("venue_broker", "tests"):
            shutil.copytree(
                ROOT / name,
                directory / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for name in ("broker_client.py", "caller-policy.toml", "pyproject.toml"):
            shutil.copy2(ROOT / name, directory / name)

        for mutation in cases:
            path = directory / "venue_broker" / mutation.file
            fixed = path.read_text(encoding="utf-8")
            changed = mutation.change(fixed)
            ast.parse(changed)  # Syntax errors do not count as a failing control.
            try:
                path.write_text(changed, encoding="utf-8")
                broken = run_tests(directory, mutation)
            finally:
                path.write_text(fixed, encoding="utf-8")
            restored = run_tests(directory, mutation)
            if broken.returncode != 1 or "FAILED " not in broken.stdout:
                raise AssertionError(
                    f"Control did not fail: {mutation.name}\n{broken.stdout}"
                    f"\n{broken.stderr}"
                )
            if restored.returncode != 0:
                raise AssertionError(
                    f"Restore did not pass: {mutation.name}\n{restored.stdout}"
                    f"\n{restored.stderr}"
                )
            print(
                f"PASS {mutation.name}: {broken.stdout.strip().splitlines()[-1]}"
                f"; restored: {restored.stdout.strip().splitlines()[-1]}",
                flush=True,
            )
    print(
        f"All {len(cases)} controls failed with the fix removed "
        "and passed after restore."
    )


if __name__ == "__main__":
    main()
