import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActivityFeed } from "./ActivityFeed";
import type { Fetcher } from "../api";
import { jsonResponse } from "../test/support";
import { restoreViewportWidth, setViewportWidth, snapshotOf } from "../test/support";

afterEach(() => {
  restoreViewportWidth();
});

describe("ActivityFeed", () => {
  it("shows the recent events with the approved titles", () => {
    render(<ActivityFeed snapshot={snapshotOf("healthy")} />);
    expect(screen.getByRole("heading", { name: "Control activity" })).toBeVisible();
    expect(screen.getByText("Possible direct caller")).toBeVisible();
    expect(screen.getByText("Broker started")).toBeVisible();
    expect(screen.getByText("Access policy")).toBeVisible();
    expect(screen.getAllByText("Application started")).toHaveLength(2);
    expect(
      screen.getByText(
        "Polymarket throttled this machine while ThrottleDeck Broker still had room.",
      ),
    ).toBeVisible();
  });

  it("marks a high-priority event with text and shape, not colour alone", () => {
    const { container } = render(<ActivityFeed snapshot={snapshotOf("suspectedDirectCaller")} />);
    expect(screen.getByText("Warning")).toBeVisible();
    expect(container.querySelector("[data-shape='severity']")).not.toBeNull();
  });

  it("surfaces an unknown event kind rather than dropping the event", () => {
    const snapshot = snapshotOf("healthy");
    render(
      <ActivityFeed
        snapshot={{
          ...snapshot,
          activity: [
            {
              at: "2026-09-13T14:24:00.000Z",
              kind: "unexpected-event",
              severity: "info",
              detail: "Something new happened.",
              appId: null,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("unexpected-event")).toBeVisible();
  });

  it("collapses to the latest warning row on a compact window and expands in place", async () => {
    setViewportWidth(720);
    render(<ActivityFeed snapshot={snapshotOf("healthy")} />);
    expect(screen.queryByRole("heading", { name: "Control activity" })).toBeVisible();
    expect(screen.getByText("Possible direct caller")).toBeVisible();
    expect(screen.queryByText("Broker started")).toBeNull();

    const toggle = screen.getByRole("button", { name: /Possible direct caller/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(screen.getByText("Broker started")).toBeVisible();
  });

  it("says so when there is nothing to report instead of showing an empty panel", () => {
    const snapshot = snapshotOf("healthy");
    render(<ActivityFeed snapshot={{ ...snapshot, activity: [] }} />);
    expect(screen.getByText("No recorded activity yet.")).toBeVisible();
  });

  it("surfaces a headroom 429 delta even before the service logs the event", () => {
    const flagged = snapshotOf("suspectedDirectCaller");
    render(
      <ActivityFeed
        snapshot={{
          ...flagged,
          activity: flagged.activity.filter(
            (event) => event.kind !== "possible-direct-caller",
          ),
        }}
      />,
    );
    expect(screen.getByText("Possible direct caller")).toBeVisible();
    expect(
      screen.getByText(
        "Polymarket throttled this machine while ThrottleDeck Broker still had room.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Broker started")).toBeVisible();
  });

  it("runs the direct-traffic audit only when the operator clicks", async () => {
    const fetcher = vi.fn<Fetcher>(async () => jsonResponse({
      schemaVersion: 1,
      status: "findings",
      sampledForMs: 2000,
      findings: [
        {
          venue: "polymarket-us",
          process_name: "python",
          pid: 123,
          observations: 5,
        },
      ],
      caveat: "Sampling can miss short connections.",
    }));
    render(<ActivityFeed snapshot={snapshotOf("healthy")} fetcher={fetcher} />);
    expect(fetcher).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Traffic audit" }));
    expect(
      await screen.findByText(/python.*PID 123.*polymarket-us/i),
    ).toBeVisible();
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
