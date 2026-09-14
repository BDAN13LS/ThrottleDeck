import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppTable } from "./AppTable";
import type { GovernorSnapshot } from "../contracts";
import { NOW, restoreViewportWidth, setViewportWidth, snapshotOf } from "../test/support";

function renderTable(
  snapshot: GovernorSnapshot = snapshotOf("healthy"),
  overrides: Partial<Parameters<typeof AppTable>[0]> = {},
) {
  const handlers = {
    onToggleDetail: vi.fn(),
    onStopApp: vi.fn(),
    onResumeApp: vi.fn(),
  };
  render(
    <AppTable
      snapshot={snapshot}
      now={NOW}
      pending={false}
      detailAppId={null}
      {...handlers}
      {...overrides}
    />,
  );
  return handlers;
}

afterEach(() => {
  restoreViewportWidth();
});

describe("AppTable", () => {
  it("uses the approved column headings on desktop", () => {
    renderTable();
    for (const heading of [
      "Name",
      "Brokered REST",
      "Recent requests",
      "Queue wait",
      "Errors",
      "Access",
    ]) {
      expect(screen.getByRole("columnheader", { name: heading })).toBeVisible();
    }
  });

  it("lists every registered application with its measurements", () => {
    renderTable();
    expect(screen.getByRole("rowheader", { name: /Signal Collector/ })).toBeVisible();
    expect(screen.getByRole("rowheader", { name: /Execution Bot/ })).toBeVisible();
    expect(screen.getByRole("rowheader", { name: /Market Research/ })).toBeVisible();
    expect(screen.getByText("Real money")).toBeVisible();
    expect(screen.getAllByText("Paper")).toHaveLength(2);
    expect(screen.getByText("12 REST / 1m")).toBeVisible();
    expect(screen.getByText("36 REST / 1m")).toBeVisible();
    expect(screen.getByText("5 ms")).toBeVisible();
    expect(screen.getAllByText("Yes")).toHaveLength(3);
  });

  it("stops one application by id and leaves the others alone", async () => {
    const handlers = renderTable();
    const row = screen.getByRole("row", { name: /Signal Collector/ });
    await userEvent.click(within(row).getByRole("button", { name: "Stop access" }));
    expect(handlers.onStopApp).toHaveBeenCalledWith("collector");
  });

  it("offers a visible resume on a stopped row", async () => {
    const base = snapshotOf("healthy");
    const stopped = {
      ...base,
      apps: base.apps.map((app) =>
        app.appId === "execution" ? { ...app, access: "stopped" as const } : app,
      ),
    };
    const handlers = renderTable(stopped);
    const row = screen.getByRole("row", { name: /Execution Bot/ });
    expect(within(row).getAllByText("Stopped").length).toBeGreaterThan(0);
    expect(within(row).queryByRole("button", { name: "Stop access" })).toBeNull();
    await userEvent.click(within(row).getByRole("button", { name: "Resume access" }));
    expect(handlers.onResumeApp).toHaveBeenCalledWith("execution");
  });

  it("expands one application detail with a keyboard-operable disclosure", async () => {
    const handlers = renderTable();
    const disclosure = screen.getByRole("button", { name: "Signal Collector details" });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(disclosure);
    expect(handlers.onToggleDetail).toHaveBeenCalledWith("collector");
  });

  it("renders the detail panel for the selected application only", () => {
    renderTable(snapshotOf("healthy"), { detailAppId: "execution" });
    expect(
      screen.getByText(
        "Blocks brokered REST reads for this application's caller labels.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Does not stop direct venue requests, WebSocket streams, or the application process.",
      ),
    ).toBeVisible();
  });

  it("keeps every measurement in a compact two-line row without extra columns", () => {
    setViewportWidth(720);
    renderTable();
    expect(screen.queryByRole("columnheader", { name: "Recent requests" })).toBeNull();
    const row = screen.getByRole("row", { name: /Signal Collector/ });
    expect(within(row).getByText("12 REST / 1m")).toBeVisible();
    expect(within(row).getByText(/0 ms/)).toBeVisible();
    expect(within(row).getByText(/0 errors/)).toBeVisible();
    expect(within(row).getByRole("button", { name: "Stop access" })).toBeVisible();
  });

  it("makes recent brokered REST movement and recency visible in each compact row", () => {
    setViewportWidth(940);
    renderTable();
    expect(screen.getByText("Brokered REST activity · WebSockets not measured")).toBeVisible();
    const row = screen.getByRole("row", { name: /Signal Collector/ });
    expect(within(row).getByText("12 REST / 1m")).toBeVisible();
    expect(within(row).getByText("Last REST now")).toBeVisible();
    expect(
      within(row).getByRole("img", {
        name: "Signal Collector brokered REST activity: 12 requests over 1m.",
      }),
    ).toBeVisible();
  });

  it("surfaces the unassigned caller bucket as its own row", () => {
    renderTable(snapshotOf("counterReset"));
    expect(screen.getByRole("rowheader", { name: /Unassigned callers/ })).toBeVisible();
  });
});
