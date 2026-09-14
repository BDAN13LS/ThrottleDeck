import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppDetail } from "./AppDetail";
import type { AppRow, GovernorSnapshot } from "../contracts";
import { NOW, snapshotOf } from "../test/support";

function appOf(snapshot: GovernorSnapshot, appId: string): AppRow {
  const app = snapshot.apps.find((entry) => entry.appId === appId);
  if (app === undefined) {
    throw new Error(`fixture has no app ${appId}`);
  }
  return app;
}

describe("AppDetail", () => {
  it("names the caller labels a stop would affect", () => {
    const snapshot = snapshotOf("healthy");
    render(
      <AppDetail
        snapshot={snapshot}
        app={appOf(snapshot, "execution")}
        now={NOW}
      />,
    );
    expect(screen.getByText("execution-watch-football")).toBeVisible();
    expect(screen.getByText("execution-reconcile")).toBeVisible();
    expect(screen.getByText("Callers")).toBeVisible();
  });

  it("states exactly what the stop does and does not affect", () => {
    const snapshot = snapshotOf("healthy");
    render(
      <AppDetail
        snapshot={snapshot}
        app={appOf(snapshot, "collector")}
        now={NOW}
      />,
    );
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
    expect(screen.queryByText(/flatten/i)).toBeNull();
  });

  it("reports the venue split and the recent measurements", () => {
    const snapshot = snapshotOf("healthy");
    render(
      <AppDetail
        snapshot={snapshot}
        app={appOf(snapshot, "collector")}
        now={NOW}
      />,
    );
    expect(screen.getByText("Venue split")).toBeVisible();
    expect(screen.getByText("polymarket-us")).toBeVisible();
    expect(screen.getByText("Recent requests")).toBeVisible();
    expect(screen.getByText("12 REST / 1m")).toBeVisible();
    expect(screen.getByText("Errors")).toBeVisible();
    expect(screen.getByText("429s")).toBeVisible();
    expect(screen.getByText("Headroom 429s")).toBeVisible();
    expect(screen.getByText("Worst queue")).toBeVisible();
  });

  it("surfaces the coverage gap instead of hiding it", () => {
    const snapshot = snapshotOf("healthy");
    render(
      <AppDetail
        snapshot={snapshot}
        app={appOf(snapshot, "research")}
        now={NOW}
      />,
    );
    expect(
      screen.getByText(
        "Kalshi reads are brokered. Its WebSocket streams remain direct by design.",
      ),
    ).toBeVisible();
  });

  it("never claims the paper research app uses real money", () => {
    const snapshot = snapshotOf("healthy");
    render(
      <AppDetail
        snapshot={snapshot}
        app={appOf(snapshot, "research")}
        now={NOW}
      />,
    );
    expect(
      screen.getByText("Paper: this application does not submit real-money orders."),
    ).toBeVisible();
    expect(document.body.textContent ?? "").not.toMatch(/Market Research.*real money/i);
  });

  it("ties the configured protected app to the money lock state", () => {
    const snapshot = snapshotOf("stopped");
    render(
      <AppDetail snapshot={snapshot} app={appOf(snapshot, "execution")} now={NOW} />,
    );
    expect(screen.getByText(/money lock, which is currently stopped/)).toBeVisible();
  });

  it("lists the audit entries that belong to the application", () => {
    const snapshot = snapshotOf("healthy");
    const withAudit = {
      ...snapshot,
      activity: [
        {
          at: "2026-09-13T14:18:31.000Z",
          kind: "access-policy",
          severity: "info" as const,
          detail: "Execution Bot brokered REST reads resumed.",
          appId: "execution",
        },
        ...snapshot.activity,
      ],
    };
    render(<AppDetail snapshot={withAudit} app={appOf(snapshot, "execution")} now={NOW} />);
    expect(screen.getByText("Audit entries")).toBeVisible();
    expect(screen.getByText("Execution Bot brokered REST reads resumed.")).toBeVisible();
  });
});
