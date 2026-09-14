import { describe, expect, it } from "vitest";

import { parseSnapshot, SNAPSHOT_SCHEMA_VERSION } from "./contracts";
import { allFixtures, fixture, healthy } from "./fixtures/snapshot";

describe("parseSnapshot", () => {
  it("accepts every fixture at the supported schema version", () => {
    for (const name of Object.keys(allFixtures) as (keyof typeof allFixtures)[]) {
      const parsed = parseSnapshot(fixture(name));
      expect(parsed.ok, name).toBe(true);
      if (parsed.ok) {
        expect(parsed.snapshot.schemaVersion).toBe(SNAPSHOT_SCHEMA_VERSION);
        expect(parsed.snapshot.budgets).toHaveLength(3);
      }
    }
  });

  it("keeps the three budget identities stable, never by array position", () => {
    const parsed = parseSnapshot({
      ...(healthy as object),
      budgets: [...(healthy as { budgets: unknown[] }).budgets].reverse(),
    });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.snapshot.budgets.map((budget) => budget.id)).toEqual([
        "pmus",
        "kalshi.predictions",
        "kalshi.perps",
      ]);
    }
  });

  it("surfaces an unknown schema version instead of parsing it", () => {
    const parsed = parseSnapshot({ ...(healthy as object), schemaVersion: 2 });
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) {
      expect(parsed.problem.kind).toBe("unsupported-schema");
      expect(parsed.problem.message).toContain("2");
      expect(parsed.problem.message).toContain("1");
    }
  });

  it("rejects a payload with no schema version", () => {
    const payload: Record<string, unknown> = { ...(healthy as object) };
    delete payload["schemaVersion"];
    const parsed = parseSnapshot(payload);
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) {
      expect(parsed.problem.kind).toBe("invalid-payload");
    }
  });

  it("rejects a payload that is not an object", () => {
    for (const payload of [null, 42, "snapshot", []]) {
      const parsed = parseSnapshot(payload);
      expect(parsed.ok).toBe(false);
    }
  });

  it("rejects a payload with missing required sections", () => {
    const payload: Record<string, unknown> = { ...(healthy as object) };
    delete payload["brokerAccess"];
    const parsed = parseSnapshot(payload);
    expect(parsed.ok).toBe(false);
  });

  it("never fabricates a budget the service did not send", () => {
    const payload = {
      ...(healthy as object),
      budgets: [
        { id: "pmus", health: "healthy", available_now: 18, capacity: 18 },
      ],
    };
    const parsed = parseSnapshot(payload);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      const perps = parsed.snapshot.budgets.find(
        (budget) => budget.id === "kalshi.perps",
      );
      expect(perps?.health).toBe("unavailable");
      expect(perps?.availableNow).toBeNull();
      expect(perps?.capacity).toBeNull();
    }
  });

  it("surfaces an unknown activity kind rather than discarding the event", () => {
    const payload = {
      ...(healthy as object),
      activity: [
        {
          at: "2026-09-13T14:20:00.000Z",
          kind: "something-new",
          severity: "warning",
          detail: "A newer service sent an event this window does not know.",
        },
      ],
    };
    const parsed = parseSnapshot(payload);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.snapshot.activity[0]?.kind).toBe("something-new");
    }
  });

  it("accepts camelCase aliases for a service that serialises them", () => {
    const parsed = parseSnapshot({
      schemaVersion: 1,
      revision: 3,
      generatedAt: "2026-09-13T14:24:01.000Z",
      broker: { state: "healthy", sampledAt: "2026-09-13T14:24:00.000Z" },
      brokerAccess: { global: { state: "allowed", revision: 3 }, apps: {} },
      tradeLock: { state: "stopped", revision: 3 },
      budgets: [],
      apps: [],
      activity: [],
    });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.snapshot.tradeLock.state).toBe("stopped");
      expect(parsed.snapshot.broker.sampledAt).toBe("2026-09-13T14:24:00.000Z");
      expect(parsed.snapshot.budgets.every((b) => b.health === "unavailable")).toBe(
        true,
      );
    }
  });

  it("drops an app row that has no identity instead of crashing", () => {
    const payload = {
      ...(healthy as object),
      apps: [{ name: "No identity" }, { app_id: "x", name: "Has identity" }],
    };
    const parsed = parseSnapshot(payload);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.snapshot.apps.map((app) => app.appId)).toEqual(["x"]);
    }
  });

  it("preserves request-series gaps instead of turning unknown traffic into zero", () => {
    const payload = structuredClone(healthy) as Record<string, any>;
    payload.apps[0].request_series = [3, null, -2, 4.4];
    const parsed = parseSnapshot(payload);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.snapshot.apps[0]?.requestSeries).toEqual([3, null, 0, 4]);
    }
  });

  it("keeps per-user money-control presentation out of frontend constants", () => {
    const payload = structuredClone(healthy) as Record<string, any>;
    payload.tradeLock = {
      ...payload.tradeLock,
      app_id: "my-trader",
      app_name: "My Trader",
      confirmation_phrase: "UNLOCK MY TRADER",
    };
    const parsed = parseSnapshot(payload);
    expect(parsed.ok).toBe(true);
    if (parsed.ok) {
      expect(parsed.snapshot.tradeLock.appName).toBe("My Trader");
      expect(parsed.snapshot.tradeLock.confirmationPhrase).toBe("UNLOCK MY TRADER");
    }
  });
});
