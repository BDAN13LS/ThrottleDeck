import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  applyControl,
  loadSnapshot,
  readCsrfToken,
  resumeBrokerAccess,
  stopBrokerAccess,
  stopNewMoneyOrders,
  unlockNewMoneyOrders,
  runTrafficAudit,
  type Fetcher,
} from "./api";
import { COPY } from "./derive";
import { healthy, stopped } from "./fixtures/snapshot";
import { jsonResponse, snapshotOf, textResponse } from "./test/support";

function fetcherOf(response: Response) {
  return vi.fn<Fetcher>(async () => response);
}

beforeEach(() => {
  document.cookie = "governor_csrf=token-abc; path=/";
});

describe("loadSnapshot", () => {
  it("parses a versioned snapshot from the loopback endpoint", async () => {
    const fetcher = fetcherOf(jsonResponse(healthy));
    const result = await loadSnapshot(fetcher);
    expect(result.ok).toBe(true);
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/snapshot");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
  });

  it("reports Governor as unreachable when the request throws", async () => {
    const fetcher = vi.fn<Fetcher>(async () => {
      throw new TypeError("network error");
    });
    const result = await loadSnapshot(fetcher);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.problem.kind).toBe("unreachable");
      expect(result.problem.message).toBe("Governor is not responding.");
    }
  });

  it("treats a server error as unreachable", async () => {
    const result = await loadSnapshot(fetcherOf(textResponse("boom", 500)));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.problem.kind).toBe("unreachable");
    }
  });

  it("rejects an unreadable body instead of showing a partial truth", async () => {
    const result = await loadSnapshot(fetcherOf(textResponse("<html>", 200)));
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.problem.kind).toBe("invalid-payload");
    }
  });

  it("surfaces an unsupported schema version", async () => {
    const result = await loadSnapshot(
      fetcherOf(jsonResponse({ ...(healthy as object), schemaVersion: 9 })),
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.problem.kind).toBe("unsupported-schema");
    }
  });
});

describe("applyControl", () => {
  it("posts the expected revision with the session cookie and CSRF header", async () => {
    const fetcher = fetcherOf(jsonResponse({ snapshot: stopped }));
    const action = stopBrokerAccess(snapshotOf("healthy"), "collector", "operator");
    const result = await applyControl(action, fetcher);

    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/broker-access/apps/collector/stop");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("token-abc");
    expect(JSON.parse(String(init.body))).toEqual({
      expected_revision: 12,
      reason: "operator",
    });
    expect(result.status).toBe("applied");
    expect(result.snapshot?.revision).toBe(14);
  });

  it("accepts a bare snapshot in the success body", async () => {
    const result = await applyControl(
      stopNewMoneyOrders(snapshotOf("healthy")),
      fetcherOf(jsonResponse(stopped)),
    );
    expect(result.status).toBe("applied");
    expect(result.snapshot?.tradeLock.state).toBe("stopped");
  });

  it("uses the configured phrase and a reason to unlock the protected app", async () => {
    const fetcher = fetcherOf(jsonResponse({ snapshot: healthy }));
    await applyControl(
      unlockNewMoneyOrders(snapshotOf("stopped"), "reviewed the incident"),
      fetcher,
    );
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/trade-lock/execution/unlock");
    expect(JSON.parse(String(init.body))).toEqual({
      expected_revision: 14,
      reason: "reviewed the incident",
      confirmation: "UNLOCK NEW ORDERS",
    });
  });

  it("reloads the authoritative snapshot on a revision conflict", async () => {
    const latest = { ...(stopped as object), revision: 30 };
    const result = await applyControl(
      stopBrokerAccess(snapshotOf("healthy"), null, "operator"),
      fetcherOf(jsonResponse({ error: "revision_conflict", snapshot: latest }, 409)),
    );
    expect(result.status).toBe("conflict");
    expect(result.snapshot?.revision).toBe(30);
    expect(result.message).toBe(COPY.notApplied);
  });

  it("keeps no snapshot when a conflict sends none", async () => {
    const result = await applyControl(
      stopBrokerAccess(snapshotOf("healthy"), null, "operator"),
      fetcherOf(jsonResponse({ error: "revision_conflict" }, 409)),
    );
    expect(result.status).toBe("conflict");
    expect(result.snapshot).toBeNull();
  });

  it("reports a refused action as not applied", async () => {
    const result = await applyControl(
      stopBrokerAccess(snapshotOf("healthy"), null, "operator"),
      fetcherOf(textResponse("forbidden", 403)),
    );
    expect(result.status).toBe("failed");
    expect(result.snapshot).toBeNull();
    expect(result.message).toBe(COPY.notApplied);
  });

  it("reports a dropped connection as not applied", async () => {
    const fetcher = vi.fn<Fetcher>(async () => {
      throw new TypeError("network error");
    });
    const result = await applyControl(
      stopBrokerAccess(snapshotOf("healthy"), null, "operator"),
      fetcher,
    );
    expect(result.status).toBe("failed");
    expect(result.message).toBe(COPY.notApplied);
  });

  it("does not trust a success body that is not a snapshot", async () => {
    const result = await applyControl(
      stopBrokerAccess(snapshotOf("healthy"), null, "operator"),
      fetcherOf(jsonResponse({ ok: true })),
    );
    expect(result.status).toBe("failed");
  });
});

describe("action builders", () => {
  it("targets explicit endpoints and never a generic toggle", () => {
    const snapshot = snapshotOf("healthy");
    expect(stopBrokerAccess(snapshot, null, "operator").endpoint).toBe(
      "/api/v1/broker-access/global/stop",
    );
    expect(resumeBrokerAccess(snapshot, null).endpoint).toBe(
      "/api/v1/broker-access/global/resume",
    );
    expect(resumeBrokerAccess(snapshot, "research").endpoint).toBe(
      "/api/v1/broker-access/apps/research/resume",
    );
    expect(stopNewMoneyOrders(snapshot).endpoint).toBe(
      "/api/v1/trade-lock/execution/stop",
    );
    for (const endpoint of [
      stopBrokerAccess(snapshot, null, "operator").endpoint,
      stopNewMoneyOrders(snapshot).endpoint,
    ]) {
      expect(endpoint).not.toMatch(/toggle/);
      expect(endpoint).toMatch(/^\/api\/v1\//);
    }
  });

  it("sends the server-required STOP ALL confirmation for the global stop", () => {
    expect(stopBrokerAccess(snapshotOf("healthy"), null, "operator").confirmation).toBe(
      "STOP ALL",
    );
  });
});

describe("runTrafficAudit", () => {
  it("runs only on demand with same-origin session and CSRF protection", async () => {
    const fetcher = fetcherOf(jsonResponse({
      schemaVersion: 1,
      status: "findings",
      sampledForMs: 2000,
      findings: [
        { venue: "kalshi", process_name: "python", pid: 44, observations: 2 },
      ],
      caveat: "Sampling can miss short connections.",
    }));
    const result = await runTrafficAudit(fetcher);
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/diagnostics/direct-egress");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token-abc");
    expect(result.status).toBe("findings");
    expect(result.findings[0]?.processName).toBe("python");
  });
});

describe("readCsrfToken", () => {
  it("reads the session token from the readable cookie", () => {
    expect(readCsrfToken()).toBe("token-abc");
  });

  it("falls back to a meta tag and then to nothing", () => {
    document.cookie = "governor_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    expect(readCsrfToken()).toBeNull();
    const meta = document.createElement("meta");
    meta.name = "governor-csrf";
    meta.content = "meta-token";
    document.head.appendChild(meta);
    expect(readCsrfToken()).toBe("meta-token");
    meta.remove();
  });
});
