import { describe, expect, it } from "vitest";

import {
  appAccessLabel,
  appMoneyLabel,
  budgetStatusLabel,
  budgetTitle,
  budgetUnitLabel,
  brokerHealthLabel,
  describeSeries,
  directCallerAlert,
  eventTitle,
  formatAge,
  formatAvailable,
  formatBrokeredRest,
  formatBudgetPressure,
  formatClock,
  formatErrors,
  formatLastRest,
  formatQueueDepth,
  formatQueueWait,
  formatRecentRequests,
  formatRefill,
  hasHeadroomDeltaAlert,
  isSnapshotStale,
  moneyLockView,
  possibleDirectCallerEvent,
} from "./derive";
import type { AppRow, Budget } from "./contracts";
import { NOW, snapshotOf } from "./test/support";

function budgetOf(name: Parameters<typeof snapshotOf>[0], id: Budget["id"]): Budget {
  const budget = snapshotOf(name).budgets.find((entry) => entry.id === id);
  if (budget === undefined) {
    throw new Error(`fixture has no budget ${id}`);
  }
  return budget;
}

function appOf(name: Parameters<typeof snapshotOf>[0], appId: string): AppRow {
  const app = snapshotOf(name).apps.find((entry) => entry.appId === appId);
  if (app === undefined) {
    throw new Error(`fixture has no app ${appId}`);
  }
  return app;
}

describe("rate budget labels", () => {
  it("names each venue with the approved product wording", () => {
    expect(budgetTitle(budgetOf("healthy", "pmus"))).toBe("Polymarket REST");
    expect(budgetTitle(budgetOf("healthy", "kalshi.predictions"))).toBe(
      "Kalshi Predictions",
    );
    expect(budgetTitle(budgetOf("healthy", "kalshi.perps"))).toBe("Kalshi Perps");
  });

  it("never labels instantaneous capacity as monthly quota", () => {
    const budget = budgetOf("healthy", "kalshi.predictions");
    expect(formatAvailable(budget)).toBe("540 / 540");
    expect(formatRefill(budget)).toBe("10 tokens/s");
    expect(formatQueueDepth(budget)).toBe("0");
    expect(formatBudgetPressure(budget)).toBe("12%");
    const rendered = [
      formatAvailable(budget),
      formatRefill(budget),
      formatQueueDepth(budget),
      formatBudgetPressure(budget),
      budgetStatusLabel(budget),
    ].join(" ");
    expect(rendered).not.toMatch(/monthly quota/i);
    expect(rendered).not.toMatch(/per month/i);
    expect(rendered).not.toMatch(/reset/i);
  });

  it("reports the Polymarket balance in requests and the Kalshi balances in tokens", () => {
    expect(budgetUnitLabel(budgetOf("healthy", "pmus"))).toBe("requests");
    expect(budgetUnitLabel(budgetOf("healthy", "kalshi.predictions"))).toBe(
      "tokens",
    );
    expect(formatRefill(budgetOf("healthy", "pmus"))).toBe("3 req/s");
  });

  it("says the account quota is unknown rather than inventing a refill", () => {
    const parsed = snapshotOf("healthy");
    const budget: Budget = {
      ...(parsed.budgets[0] as Budget),
      refillPerSecond: null,
    };
    expect(formatRefill(budget)).toBe("Account quota unknown");
  });

  it("reports an unconfigured product without inventing a number", () => {
    const perps = budgetOf("unconfiguredPerps", "kalshi.perps");
    expect(budgetStatusLabel(perps)).toBe("Not configured");
    expect(formatAvailable(perps)).toBe("Unavailable");
    expect(formatQueueDepth(perps)).toBe("Unknown");
    expect(formatBudgetPressure(perps)).toBe("Unknown");
  });

  it("never shows a missing balance as zero after the broker goes down", () => {
    const pmus = budgetOf("brokerDown", "pmus");
    expect(formatAvailable(pmus)).toBe("Unavailable");
    expect(formatAvailable(pmus)).not.toBe("0 / 0");
    expect(budgetStatusLabel(pmus)).toBe("Unavailable");
  });

  it("describes the sparkline in text for screen readers", () => {
    const summary = describeSeries(budgetOf("healthy", "pmus"));
    expect(summary).toContain("Polymarket REST");
    expect(summary).toContain("requests");
    expect(describeSeries(budgetOf("unconfiguredPerps", "kalshi.perps"))).toContain(
      "no recent sample",
    );
  });
});

describe("sample age and staleness", () => {
  it("labels fresh data as updated now", () => {
    expect(formatAge("2026-09-13T14:24:00.000Z", NOW)).toBe("Updated now");
  });

  it("reports the real age in seconds and minutes", () => {
    expect(formatAge("2026-09-13T14:23:49.000Z", NOW)).toBe("Updated 12s ago");
    expect(formatAge("2026-09-13T14:20:01.000Z", NOW)).toBe("Updated 4m ago");
  });

  it("never claims freshness when the age is unknown", () => {
    expect(formatAge(null, NOW)).toBe("Unknown");
  });

  it("marks a sample older than three polls as stale", () => {
    const stale = snapshotOf("healthy");
    expect(isSnapshotStale(stale, NOW)).toBe(false);
    expect(
      isSnapshotStale(
        { ...stale, broker: { ...stale.broker, sampledAt: "2026-09-13T14:23:30.000Z" } },
        NOW,
      ),
    ).toBe(true);
  });

  it("never shows stale data as current", () => {
    const stale = snapshotOf("healthy");
    const aged = {
      ...stale,
      broker: { ...stale.broker, sampledAt: "2026-09-13T14:21:00.000Z" },
    };
    expect(brokerHealthLabel(aged, NOW)).not.toBe("ThrottleDeck Broker healthy");
    expect(formatAge(aged.broker.sampledAt, NOW)).not.toBe("Updated now");
  });

  it("treats a stopped broker as its own state, not merely stale", () => {
    const down = snapshotOf("brokerDown");
    expect(isSnapshotStale(down, NOW)).toBe(false);
    expect(brokerHealthLabel(down, NOW)).toBe("ThrottleDeck Broker is not responding");
  });

  it("reports an older broker build as needing an update, not as offline", () => {
    const current = snapshotOf("healthy");
    const outdated = {
      ...current,
      broker: { ...current.broker, state: "degraded" as const },
    };
    expect(brokerHealthLabel(outdated, NOW)).toBe("ThrottleDeck Broker update required");
  });
});

describe("money lock copy", () => {
  it("states what is allowed and what the stop button does", () => {
    const view = moneyLockView(snapshotOf("healthy").tradeLock);
    expect(view.stateLabel).toBe("Allowed");
    expect(view.detail).toBe("Execution Bot may submit new real-money orders.");
    expect(view.actionLabel).toBe("Stop new money orders");
    expect(view.banner).toBeNull();
  });

  it("keeps the stopped copy exact and keeps a recovery action visible", () => {
    const view = moneyLockView(snapshotOf("stopped").tradeLock);
    expect(view.stateLabel).toBe("Stopped");
    expect(view.detail).toBe("Existing orders were not cancelled.");
    expect(view.banner).toBe(
      "Execution Bot cannot submit new money orders. Existing orders were not cancelled.",
    );
    expect(view.actionLabel).toBe("Unlock");
    expect(view.canUnlock).toBe(true);
  });

  it("reports a stopping lock without claiming it is finished", () => {
    const view = moneyLockView({ ...snapshotOf("healthy").tradeLock, state: "stopping" });
    expect(view.stateLabel).toBe("Stopping");
    expect(view.actionLabel).toBe("Stop new money orders");
    expect(view.canUnlock).toBe(false);
  });

  it("fails closed when the lock state cannot be proven", () => {
    const view = moneyLockView(snapshotOf("brokerDown").tradeLock);
    expect(view.stateLabel).toBe("Unavailable");
    expect(view.detail).toBe(
      "ThrottleDeck Broker is not responding. New Execution Bot orders remain locked unless Governor can prove otherwise.",
    );
    expect(view.canUnlock).toBe(false);
    expect(view.canStop).toBe(true);
  });

  it("never claims a stop cancelled anything", () => {
    for (const state of ["allowed", "stopped", "stopping", "unavailable"] as const) {
      const view = moneyLockView({ ...snapshotOf("stopped").tradeLock, state });
      const rendered = [view.detail, view.banner ?? "", view.actionLabel].join(" ");
      expect(rendered).not.toMatch(/cancelled (the|your|all|existing orders were)/i);
    }
  });
});

describe("application rows", () => {
  it("labels money mode and access with the approved words", () => {
    expect(appMoneyLabel(appOf("healthy", "execution"))).toBe("Real money");
    expect(appMoneyLabel(appOf("healthy", "collector"))).toBe("Paper");
    expect(appMoneyLabel(appOf("healthy", "research"))).toBe("Paper");
    expect(appAccessLabel(appOf("healthy", "collector"))).toBe("Allowed");
  });

  it("formats the measured columns", () => {
    const collector = appOf("healthy", "collector");
    expect(formatRecentRequests(collector)).toBe("12 REST / 1m");
    expect(formatLastRest(collector, NOW)).toBe("Last REST now");
    expect(formatQueueWait(collector)).toBe("0 ms");
    expect(formatErrors(collector)).toBe("0");
    expect(formatBrokeredRest(collector)).toBe("Yes");
  });

  it("shows unknown rather than zero when a measurement is missing", () => {
    const app: AppRow = {
      ...appOf("healthy", "collector"),
      recentRequests: null,
      queueWaitMs: null,
      errors: null,
    };
    expect(formatRecentRequests(app)).toBe("REST traffic unavailable");
    expect(formatQueueWait(app)).toBe("Unknown");
    expect(formatErrors(app)).toBe("Unknown");
  });

  it("distinguishes an old REST request from no recorded request", () => {
    const app = appOf("healthy", "collector");
    expect(
      formatLastRest(
        { ...app, lastRequestAt: "2026-09-13T14:22:01.000Z" },
        NOW,
      ),
    ).toBe("Last REST 2m ago");
    expect(formatLastRest({ ...app, lastRequestAt: null }, NOW)).toBe(
      "No REST recorded",
    );
  });

  it("surfaces unassigned callers instead of guessing a prefix", () => {
    const rows = snapshotOf("counterReset").apps;
    const unassigned = rows.find((row) => row.unassigned);
    expect(unassigned?.appId).toBe("unassigned");
    expect(unassigned?.coverageNote).toMatch(/registry does not know/i);
  });
});

describe("activity", () => {
  it("uses the approved title for each known event kind", () => {
    const events = snapshotOf("healthy").activity;
    const titles = events.map(eventTitle);
    expect(titles).toContain("Possible direct caller");
    expect(titles).toContain("Broker started");
    expect(titles).toContain("Access policy");
    expect(titles).toContain("Application started");
  });

  it("surfaces an unknown event kind rather than hiding it", () => {
    expect(
      eventTitle({
        at: "2026-09-13T14:24:00.000Z",
        kind: "brand-new-kind",
        severity: "info",
        detail: null,
        appId: null,
      }),
    ).toBe("brand-new-kind");
  });

  it("raises the direct-caller alert from a 429 with broker headroom", () => {
    expect(hasHeadroomDeltaAlert(snapshotOf("healthy"))).toBe(false);
    expect(hasHeadroomDeltaAlert(snapshotOf("suspectedDirectCaller"))).toBe(true);
    expect(directCallerAlert(snapshotOf("suspectedDirectCaller"))).toBe(true);
    expect(
      possibleDirectCallerEvent(snapshotOf("suspectedDirectCaller"))?.detail,
    ).toBe("Polymarket throttled this machine while ThrottleDeck Broker still had room.");
  });

  it("raises the alert from a warning event alone", () => {
    const flagged = snapshotOf("healthy");
    expect(hasHeadroomDeltaAlert(flagged)).toBe(false);
    expect(directCallerAlert(flagged)).toBe(true);
  });

  it("formats an event clock time", () => {
    expect(formatClock("2026-09-13T14:24:17.000Z")).toMatch(/^\d\d:\d\d:\d\d$/);
    expect(formatClock("not a time")).toBe("Unknown");
  });
});
