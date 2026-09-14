/** Shared helpers for the unit suite. Tests only; never imported by the app. */

import { parseSnapshot, type GovernorSnapshot } from "../contracts";
import { fixture, type FixtureName } from "../fixtures/snapshot";

/** A fixed clock so age labels are deterministic. */
export const NOW = Date.parse("2026-09-13T14:24:01.000Z");

export function clock(): number {
  return NOW;
}

export function snapshotOf(name: FixtureName): GovernorSnapshot {
  const parsed = parseSnapshot(fixture(name));
  if (!parsed.ok) {
    throw new Error(`fixture ${name} is not a valid snapshot: ${parsed.problem.message}`);
  }
  return parsed.snapshot;
}

/** A snapshot with one field group replaced, re-validated like a real payload. */
export function withChanges(
  name: FixtureName,
  changes: Record<string, unknown>,
): GovernorSnapshot {
  const parsed = parseSnapshot({ ...(fixture(name) as object), ...changes });
  if (!parsed.ok) {
    throw new Error(`fixture ${name} is not a valid snapshot: ${parsed.problem.message}`);
  }
  return parsed.snapshot;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function textResponse(body: string, status = 200): Response {
  return new Response(body, { status });
}

/**
 * Set the simulated window width and notify listeners, which is how the
 * compact layout is selected.
 */
export function setViewportWidth(width: number): void {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: width,
  });
  window.dispatchEvent(new Event("resize"));
}

export function restoreViewportWidth(): void {
  setViewportWidth(1024);
}
