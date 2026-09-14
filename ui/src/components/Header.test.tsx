import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Header } from "./Header";
import { NOW, snapshotOf, withChanges } from "../test/support";

describe("Header", () => {
  it("carries only product identity, broker state, update age, and refresh", () => {
    render(
      <Header
        snapshot={snapshotOf("healthy")}
        now={NOW}
        refreshing={false}
        onRefresh={() => undefined}
      />,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "ThrottleDeck — API Control",
    );
    expect(screen.getByText("ThrottleDeck Broker healthy")).toBeVisible();
    expect(screen.getByText("Updated now")).toBeVisible();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  it("shows the real age and never claims freshness for a stale sample", () => {
    const stale = withChanges("healthy", {
      broker: {
        state: "healthy",
        sampled_at: "2026-09-13T14:21:01.000Z",
      },
    });
    render(
      <Header
        snapshot={stale}
        now={NOW}
        refreshing={false}
        onRefresh={() => undefined}
      />,
    );
    expect(screen.getByText("ThrottleDeck Broker state is stale")).toBeVisible();
    expect(screen.getByText("Updated 3m ago")).toBeVisible();
    expect(screen.queryByText("Updated now")).toBeNull();
  });

  it("names an unreachable broker instead of showing it as healthy", () => {
    render(
      <Header
        snapshot={snapshotOf("brokerDown")}
        now={NOW}
        refreshing={false}
        onRefresh={() => undefined}
      />,
    );
    expect(screen.getByText("ThrottleDeck Broker is not responding")).toBeVisible();
    expect(screen.queryByText("ThrottleDeck Broker healthy")).toBeNull();
  });

  it("requests a fresh sample and reports that a refresh is in flight", async () => {
    const onRefresh = vi.fn();
    const { rerender } = render(
      <Header
        snapshot={snapshotOf("healthy")}
        now={NOW}
        refreshing={false}
        onRefresh={onRefresh}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledOnce();

    rerender(
      <Header
        snapshot={snapshotOf("healthy")}
        now={NOW}
        refreshing
        onRefresh={onRefresh}
      />,
    );
    expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
  });

  it("reports a paused history collector without hiding the controls", () => {
    const paused = withChanges("healthy", {
      broker: {
        state: "healthy",
        sampled_at: "2026-09-13T14:24:00.000Z",
        collection_paused: true,
        pause_reason: "Database size guard could not restore compliance.",
      },
    });
    render(
      <Header
        snapshot={paused}
        now={NOW}
        refreshing={false}
        onRefresh={() => undefined}
      />,
    );
    expect(
      screen.getByText(/Database size guard could not restore compliance/),
    ).toBeVisible();
  });
});
