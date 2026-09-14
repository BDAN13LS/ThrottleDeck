import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import type { Fetcher } from "./api";
import type { GovernorSnapshot } from "./contracts";
import { brokerDown, healthy, stopped } from "./fixtures/snapshot";
import { NOW, jsonResponse, snapshotOf, withChanges } from "./test/support";

type MockFetcher = Fetcher & {
  mock: { calls: [input: string, init?: RequestInit | undefined][] };
};

function snapshotFetcher(snapshot: unknown): MockFetcher {
  return vi.fn<Fetcher>(async (_input, init) =>
    init?.method === "POST" ? jsonResponse(snapshot) : jsonResponse(snapshot),
  ) as MockFetcher;
}

/** A read fixture and a separate committed result for a mutation. */
function pairFetcher(read: unknown, committed: unknown): MockFetcher {
  return vi.fn<Fetcher>(async (_input, init) =>
    init?.method === "POST"
      ? jsonResponse({ snapshot: committed })
      : jsonResponse(read),
  ) as MockFetcher;
}

function appStopped(snapshot: GovernorSnapshot, appId: string): GovernorSnapshot {
  return {
    ...snapshot,
    revision: snapshot.revision + 1,
    apps: snapshot.apps.map((app) =>
      app.appId === appId ? { ...app, access: "stopped" as const } : app,
    ),
    brokerAccess: {
      global: snapshot.brokerAccess.global,
      apps: {
        ...snapshot.brokerAccess.apps,
        [appId]: { ...snapshot.brokerAccess.apps[appId], state: "stopped" as const, revision: snapshot.revision + 1 } as never,
      },
    },
  };
}

function renderApp(fetcher: Fetcher, pollMs = 60_000) {
  return render(<App fetcher={fetcher} pollMs={pollMs} clock={() => NOW} />);
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason?: unknown) => void = () => undefined;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

describe("App", () => {
  it("renders the five zones of the primary screen", async () => {
    renderApp(snapshotFetcher(healthy));
    expect(
      await screen.findByRole("heading", { level: 1 }),
    ).toHaveTextContent("ThrottleDeck — API Control");
    expect(screen.getByText("Execution Bot new money orders")).toBeVisible();
    expect(screen.getByText("Polymarket REST")).toBeVisible();
    expect(screen.getByText("Kalshi Predictions")).toBeVisible();
    expect(screen.getByText("Kalshi Perps")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Applications" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Control activity" })).toBeVisible();
    expect(screen.getByText("Signal Collector")).toBeVisible();
    expect(screen.getByText("Market Research")).toBeVisible();
  });

  it("has no detectable accessibility violations in the healthy state", async () => {
    const { container } = renderApp(snapshotFetcher(healthy));
    await screen.findByText("Signal Collector");
    expect((await axe(container)).violations).toEqual([]);
  });

  it("keeps recovery visible and fabricates nothing when the broker is down", async () => {
    const { container } = renderApp(snapshotFetcher(brokerDown));
    expect(
      await screen.findByText("ThrottleDeck Broker is not responding"),
    ).toBeVisible();
    expect(
      screen.getByText(
        "ThrottleDeck Broker is not responding. New Execution Bot orders remain locked unless Governor can prove otherwise.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("0 / 0")).toBeNull();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    expect((await axe(container)).violations).toEqual([]);
  });

  it("reports a stale sample with its real age instead of as current", async () => {
    const stale = withChanges("healthy", {
      broker: { state: "healthy", sampled_at: "2026-09-13T14:20:01.000Z" },
    });
    renderApp(snapshotFetcher(stale));
    expect(await screen.findByText("ThrottleDeck Broker state is stale")).toBeVisible();
    expect(screen.queryByText("Updated now")).toBeNull();
  });

  it("reports Governor itself as unreachable with a visible retry", async () => {
    const fetcher = vi.fn<Fetcher>(async () => {
      throw new TypeError("network error");
    });
    renderApp(fetcher);
    expect(await screen.findByText("Governor is not responding.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });

  it("never updates a stop optimistically and applies only the committed revision", async () => {
    const user = userEvent.setup();
    let releasePost: (response: Response) => void = () => undefined;
    const pendingPost = new Promise<Response>((resolve) => {
      releasePost = resolve;
    });
    const fetcher = vi.fn<Fetcher>(async (_input, init) => {
      if (init?.method === "POST") {
        return pendingPost;
      }
      return jsonResponse(healthy);
    });
    renderApp(fetcher);

    const row = await screen.findByRole("row", { name: /Signal Collector/ });
    expect(within(row).getByText("Allowed")).toBeVisible();
    await user.click(within(row).getByRole("button", { name: "Stop access" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleName("Stop brokered REST access");
    expect(within(dialog).getByText("collector-scanner")).toBeVisible();
    await user.click(within(dialog).getByRole("button", { name: "Stop access" }));

    const post = fetcher.mock.calls.find((call) => call[1]?.method === "POST");
    expect(post?.[0]).toBe("/api/v1/broker-access/apps/collector/stop");
    expect(JSON.parse(String(post?.[1]?.body))["expected_revision"]).toBe(12);

    expect(within(row).getByText("Allowed")).toBeVisible();

    releasePost(
      jsonResponse({ snapshot: appStopped(snapshotOf("healthy"), "collector") }),
    );
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("row", { name: /Signal Collector/ }),
        ).getAllByText("Stopped").length,
      ).toBeGreaterThan(0),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reloads the latest state when the service reports a revision conflict", async () => {
    const user = userEvent.setup();
    const latest = { ...(stopped as object), revision: 30 };
    const fetcher = vi.fn<Fetcher>(async (_input, init) => {
      if (init?.method === "POST") {
        return jsonResponse({ error: "revision_conflict", snapshot: latest }, 409);
      }
      return jsonResponse(healthy);
    });
    renderApp(fetcher);

    const row = await screen.findByRole("row", { name: /Signal Collector/ });
    await user.click(within(row).getByRole("button", { name: "Stop access" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Stop access",
      }),
    );

    expect(
      await screen.findByText("Not applied — Governor did not confirm the change."),
    ).toBeVisible();
    expect(
      within(screen.getByRole("row", { name: /Signal Collector/ })).getByText("Allowed"),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("group", { name: "Execution Bot new money orders" }),
        ).getByText("Stopped"),
      ).toBeVisible(),
    );
  });

  it("leaves the previous state untouched when a mutation is refused", async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn<Fetcher>(async (_input, init) => {
      if (init?.method === "POST") {
        return new Response("forbidden", { status: 403 });
      }
      return jsonResponse(healthy);
    });
    renderApp(fetcher);

    const row = await screen.findByRole("row", { name: /Signal Collector/ });
    await user.click(within(row).getByRole("button", { name: "Stop access" }));
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Stop access",
      }),
    );

    expect(
      await screen.findByText("Not applied — Governor did not confirm the change."),
    ).toBeVisible();
    expect(
      within(screen.getByRole("row", { name: /Signal Collector/ })).getByText("Allowed"),
    ).toBeVisible();
    expect(
      within(screen.getByRole("row", { name: /Signal Collector/ })).getByRole("button", {
        name: "Stop access",
      }),
    ).toBeEnabled();
  });

  it("requires the configured phrase and a reason before unlocking", async () => {
    const user = userEvent.setup();
    const fetcher = pairFetcher(stopped, healthy);
    renderApp(fetcher);

    await user.click(await screen.findByRole("button", { name: "Unlock" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleName("Unlock Execution Bot new money orders");

    const confirm = within(dialog).getByRole("button", { name: "Unlock" });
    expect(confirm).toBeDisabled();
    await user.type(
      within(dialog).getByLabelText(/Type UNLOCK NEW ORDERS to confirm/),
      "UNLOCK NEW ORDERS",
    );
    await user.type(
      within(dialog).getByLabelText(/Reason/),
      "Reviewed the direct-caller warning.",
    );
    await user.click(confirm);

    const post = fetcher.mock.calls.find((call) => call[1]?.method === "POST");
    expect(post?.[0]).toBe("/api/v1/trade-lock/execution/unlock");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      expected_revision: 14,
      reason: "Reviewed the direct-caller warning.",
      confirmation: "UNLOCK NEW ORDERS",
    });
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("group", { name: "Execution Bot new money orders" }),
        ).getByText("Allowed"),
      ).toBeVisible(),
    );
  });

  it("requires STOP ALL before stopping machine-wide broker access", async () => {
    const user = userEvent.setup();
    renderApp(snapshotFetcher(healthy));

    await user.click(
      await screen.findByRole("button", { name: "Stop all brokered REST access" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("button", { name: "Stop all brokered REST access" }),
    ).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Type STOP ALL to confirm/), "STOP ALL");
    expect(
      within(dialog).getByRole("button", { name: "Stop all brokered REST access" }),
    ).toBeEnabled();
  });

  it("polls the local service again without ever calling a venue", async () => {
    const fetcher = snapshotFetcher(healthy);
    renderApp(fetcher, 40);
    await screen.findByText("Signal Collector");
    await waitFor(() => expect(fetcher.mock.calls.length).toBeGreaterThan(1));
    for (const [url] of fetcher.mock.calls) {
      expect(url).toBe("/api/v1/snapshot");
    }
  });

  it("keeps one transient automatic poll failure from flashing an outage", async () => {
    const failedPoll = deferred<Response>();
    const laterPoll = deferred<Response>();
    let calls = 0;
    const fetcher = vi.fn<Fetcher>(async () => {
      calls += 1;
      if (calls === 1) {
        return jsonResponse(healthy);
      }
      if (calls === 2) {
        return failedPoll.promise;
      }
      return laterPoll.promise;
    });

    renderApp(fetcher, 100);
    await screen.findByText("Signal Collector");
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await act(async () => {
      failedPoll.reject(new TypeError("brief connection reset"));
      await Promise.resolve();
    });

    expect(screen.queryByText(/This window is showing the last good sample/)).toBeNull();
  });

  it("shows an outage after two consecutive automatic poll failures", async () => {
    let calls = 0;
    const never = deferred<Response>();
    const fetcher = vi.fn<Fetcher>(async () => {
      calls += 1;
      if (calls === 1) {
        return jsonResponse(healthy);
      }
      if (calls === 2 || calls === 3) {
        throw new TypeError("connection refused");
      }
      return never.promise;
    });

    renderApp(fetcher, 40);
    expect(await screen.findByText("Signal Collector")).toBeVisible();
    expect(
      await screen.findByText(/This window is showing the last good sample/),
    ).toBeVisible();
  });

  it("does not let an older failed poll overwrite a newer manual refresh", async () => {
    const user = userEvent.setup();
    const olderPoll = deferred<Response>();
    const never = deferred<Response>();
    let calls = 0;
    const fetcher = vi.fn<Fetcher>(async () => {
      calls += 1;
      if (calls === 1) {
        return jsonResponse(healthy);
      }
      if (calls === 2) {
        return olderPoll.promise;
      }
      if (calls === 3) {
        return jsonResponse(stopped);
      }
      return never.promise;
    });

    renderApp(fetcher, 100);
    await screen.findByText("Signal Collector");
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("group", { name: "Execution Bot new money orders" }),
        ).getByText("Stopped"),
      ).toBeVisible(),
    );

    await act(async () => {
      olderPoll.reject(new TypeError("late failure"));
      await Promise.resolve();
    });

    expect(screen.queryByText(/This window is showing the last good sample/)).toBeNull();
  });

  it("surfaces an unsupported snapshot version instead of guessing", async () => {
    renderApp(snapshotFetcher({ ...(healthy as object), schemaVersion: 3 }));
    expect(
      await screen.findByText(/Unsupported snapshot version 3/),
    ).toBeVisible();
  });
});
