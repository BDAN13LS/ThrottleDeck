import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SafetyStrip } from "./SafetyStrip";
import { snapshotOf, withChanges } from "../test/support";

function renderStrip(
  name: Parameters<typeof snapshotOf>[0],
  overrides: Partial<Parameters<typeof SafetyStrip>[0]> = {},
) {
  const handlers = {
    onStopNewMoneyOrders: vi.fn(),
    onUnlock: vi.fn(),
    onStopAllAccess: vi.fn(),
    onResumeAllAccess: vi.fn(),
  };
  render(
    <SafetyStrip
      snapshot={snapshotOf(name)}
      pending={false}
      {...handlers}
      {...overrides}
    />,
  );
  return handlers;
}

function moneyGroup(): HTMLElement {
  return screen.getByRole("group", { name: "Execution Bot new money orders" });
}

describe("SafetyStrip", () => {
  it("leads with the money stop and keeps the global read stop separate", () => {
    const handlers = renderStrip("healthy");
    expect(screen.getByText("Execution Bot new money orders")).toBeVisible();
    expect(within(moneyGroup()).getByText("Allowed")).toBeVisible();
    expect(
      screen.getByText("Execution Bot may submit new real-money orders."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Stop new money orders" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Stop all brokered REST access" }),
    ).toBeEnabled();
    expect(handlers.onStopNewMoneyOrders).not.toHaveBeenCalled();
  });

  it("stops new money orders with one click and never optimistically", async () => {
    const handlers = renderStrip("healthy");
    await userEvent.click(screen.getByRole("button", { name: "Stop new money orders" }));
    expect(handlers.onStopNewMoneyOrders).toHaveBeenCalledOnce();
    expect(within(moneyGroup()).getByText("Allowed")).toBeVisible();
  });

  it("keeps the stopped state, its exact copy, and a visible recovery action", () => {
    renderStrip("stopped");
    expect(within(moneyGroup()).getByText("Stopped")).toBeVisible();
    expect(screen.getByText("Existing orders were not cancelled.")).toBeVisible();
    expect(
      screen.getByText(
        "Execution Bot cannot submit new money orders. Existing orders were not cancelled.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Unlock" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Stop new money orders" }),
    ).toBeNull();
  });

  it("reports a resolving stop as stopping", () => {
    renderStrip("healthy", {
      snapshot: withChanges("healthy", {
        tradeLock: {
          scope: "trade:new-orders:execution",
          app_id: "execution",
          app_name: "Execution Bot",
          confirmation_phrase: "UNLOCK NEW ORDERS",
          state: "stopping",
          revision: 12,
        },
      }),
    });
    expect(within(moneyGroup()).getByText("Stopping")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Unlock" })).toBeNull();
  });

  it("fails closed when the lock cannot be proven", () => {
    renderStrip("brokerDown");
    expect(within(moneyGroup()).getByText("Unavailable")).toBeVisible();
    expect(
      screen.getByText(
        "VenueBroker is not responding. New Execution Bot orders remain locked unless Governor can prove otherwise.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Unlock" })).toBeNull();
  });

  it("converts the global control to a visible resume when reads are stopped", async () => {
    const base = snapshotOf("healthy");
    const stoppedReads = withChanges("healthy", {
      brokerAccess: {
        global: { scope: "broker:global", state: "stopped", revision: 13 },
        apps: base.brokerAccess.apps,
      },
    });
    const handlers = renderStrip("healthy", { snapshot: stoppedReads });
    expect(
      screen.queryByRole("button", { name: "Stop all brokered REST access" }),
    ).toBeNull();
    const resume = screen.getByRole("button", {
      name: "Resume all brokered REST access",
    });
    await userEvent.click(resume);
    expect(handlers.onResumeAllAccess).toHaveBeenCalledOnce();
  });

  it("states the exact scope of a broker-access stop in both directions", () => {
    const base = snapshotOf("healthy");
    renderStrip("healthy", {
      snapshot: withChanges("healthy", {
        brokerAccess: {
          global: { scope: "broker:global", state: "stopped", revision: 13 },
          apps: base.brokerAccess.apps,
        },
      }),
    });
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/every brokered REST read on this machine stops/i);
    expect(text).toMatch(/direct venue requests and WebSocket streams are not affected/i);
    expect(text).not.toMatch(/stopped the application/i);
    expect(text).not.toMatch(/stopped live trading/i);
  });

  it("disables both controls while a decision is in flight", () => {
    renderStrip("healthy", { pending: true });
    expect(screen.getByRole("button", { name: "Stop new money orders" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Stop all brokered REST access" }),
    ).toBeDisabled();
  });
});
