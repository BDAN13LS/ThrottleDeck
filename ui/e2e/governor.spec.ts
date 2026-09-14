/**
 * Browser interaction tests for the built Governor dashboard.
 *
 * They run against the real production bundle and a fake Governor service
 * installed with route interception. No venue is contacted, no control state is
 * written anywhere, and every response is owned by the test.
 */

import path from "node:path";

import { expect, test, type Page, type Route } from "@playwright/test";

import {
  brokerDown,
  counterReset,
  healthy,
  stopped as stoppedFixture,
  unconfiguredPerps,
  warning,
} from "../src/fixtures/snapshot";

type AccessState = "allowed" | "stopped";

/** A small in-memory stand-in for the Governor control API. */
class FakeGovernor {
  revision = 12;
  /** null keeps whatever the active fixture already declares. */
  tradeLock: "allowed" | "stopped" | null = null;
  global: AccessState = "allowed";
  brokerState: "healthy" | "down" = "healthy";
  apps: Record<string, AccessState> = {
    collector: "allowed",
    execution: "allowed",
    research: "allowed",
  };
  base: unknown = healthy;
  sampledAt = (): string => new Date().toISOString();
  mutationDelayMs = 0;
  failMutation = false;
  conflictMutation = false;
  getFailuresRemaining = 0;
  getFailureCount = 0;

  readonly posts: { path: string; body: Record<string, unknown> }[] = [];

  install(page: Page): Promise<unknown> {
    return page.route("**/api/v1/**", (route) => this.handle(route));
  }

  private build(): Record<string, unknown> {
    const base = structuredClone(this.base) as Record<string, any>;
    const now = this.sampledAt();
    base.generatedAt = now;
    base.revision = this.revision;
    base.broker = {
      ...base.broker,
      state: this.brokerState,
      sampled_at: now,
      sample_age_ms: 0,
    };
    if (this.tradeLock !== null) {
      base.tradeLock = {
        ...base.tradeLock,
        state: this.tradeLock,
        revision: this.revision,
      };
    }
    base.brokerAccess = {
      ...base.brokerAccess,
      global: { ...base.brokerAccess.global, state: this.global, revision: this.revision },
      apps: { ...base.brokerAccess.apps },
    };
    for (const [appId, state] of Object.entries(this.apps)) {
      base.brokerAccess.apps[appId] = {
        ...base.brokerAccess.apps[appId],
        state,
        revision: this.revision,
      };
      const row = (base.apps as Record<string, any>[]).find(
        (entry) => entry.app_id === appId,
      );
      if (row !== undefined) {
        row.access = state;
        row.last_request_at = now;
      }
    }
    return base;
  }

  private apply(path: string, body: Record<string, unknown>): boolean {
    const expected = body["expected_revision"];
    if (typeof expected === "number" && expected !== this.revision) {
      return false;
    }
    const appMatch = /^\/api\/v1\/broker-access\/apps\/([^/]+)\/(stop|resume)$/.exec(path);
    if (path === "/api/v1/broker-access/global/stop") {
      this.global = "stopped";
    } else if (path === "/api/v1/broker-access/global/resume") {
      this.global = "allowed";
    } else if (appMatch !== null) {
      const appId = appMatch[1] as string;
      this.apps[appId] = appMatch[2] === "stop" ? "stopped" : "allowed";
    } else if (path === "/api/v1/trade-lock/execution/stop") {
      this.tradeLock = "stopped";
    } else if (path === "/api/v1/trade-lock/execution/unlock") {
      if (body["confirmation"] !== "UNLOCK NEW ORDERS") {
        return false;
      }
      this.tradeLock = "allowed";
    }
    this.revision += 1;
    return true;
  }

  private async handle(route: Route): Promise<void> {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (request.method() === "GET") {
      if (this.getFailuresRemaining > 0) {
        this.getFailuresRemaining -= 1;
        this.getFailureCount += 1;
        await route.abort("connectionrefused");
        return;
      }
      await route.fulfill({ json: this.build() });
      return;
    }

    const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
    this.posts.push({ path, body });
    if (this.mutationDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, this.mutationDelayMs));
    }
    if (this.failMutation) {
      await route.fulfill({ status: 403, body: "forbidden" });
      return;
    }
    if (this.conflictMutation) {
      this.revision += 5;
      await route.fulfill({
        status: 409,
        json: { error: "revision_conflict", snapshot: this.build() },
      });
      return;
    }
    if (!this.apply(path, body)) {
      await route.fulfill({
        status: 409,
        json: { error: "revision_conflict", snapshot: this.build() },
      });
      return;
    }
    await route.fulfill({ json: { snapshot: this.build() } });
  }
}

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => ({
    document:
      document.documentElement.scrollWidth - document.documentElement.clientWidth,
    body: document.body.scrollWidth - document.body.clientWidth,
  }));
  expect(overflow.document).toBeLessThanOrEqual(1);
  expect(overflow.body).toBeLessThanOrEqual(1);
}

async function expectNoPageOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => ({
    documentWidth:
      document.documentElement.scrollWidth - document.documentElement.clientWidth,
    documentHeight:
      document.documentElement.scrollHeight - document.documentElement.clientHeight,
    bodyWidth: document.body.scrollWidth - document.body.clientWidth,
    bodyHeight: document.body.scrollHeight - document.body.clientHeight,
  }));
  expect(overflow).toEqual({
    documentWidth: 0,
    documentHeight: 0,
    bodyWidth: 0,
    bodyHeight: 0,
  });
}

test.describe("Governor dashboard", () => {
  let governor: FakeGovernor;

  test.beforeEach(async ({ page }) => {
    governor = new FakeGovernor();
    await governor.install(page);
    await page.goto("/");
  });

  test("shows every zone in the primary screen without horizontal overflow", async (
    { page },
    testInfo,
  ) => {
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "ThrottleDeck — API Control",
    );
    await expect(page.getByText("ThrottleDeck Broker healthy")).toBeVisible();
    await expect(page.getByText("Updated now")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Execution Bot new money orders" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Stop new money orders" }),
    ).toBeVisible();
    for (const title of ["Polymarket REST", "Kalshi Predictions", "Kalshi Perps"]) {
      await expect(page.getByRole("heading", { name: title })).toBeVisible();
      await expect(page.getByText("Available now").first()).toBeVisible();
    }
    await expect(page.getByRole("heading", { name: "Applications" })).toBeVisible();
    for (const app of ["Signal Collector", "Execution Bot", "Market Research"]) {
      await expect(page.getByRole("rowheader", { name: new RegExp(app) })).toBeVisible();
    }
    await expect(page.getByRole("heading", { name: "Control activity" })).toBeVisible();
    await expectNoHorizontalOverflow(page);
    if (
      process.env["THROTTLEDECK_DOCS_SCREENSHOT"] === "1" &&
      testInfo.project.name === "window-940x700"
    ) {
      await page.screenshot({
        path: path.resolve(
          process.cwd(),
          "..",
          "docs",
          "design",
          "governor",
          "governor-dark-940x700.png",
        ),
      });
    }
  });

  test("uses the approved cool near-black theme", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Polymarket REST" })).toBeVisible();
    const palette = await page.evaluate(() => {
      const body = getComputedStyle(document.body);
      const card = getComputedStyle(document.querySelector(".card") as HTMLElement);
      return {
        canvas: body.backgroundColor,
        surface: card.backgroundColor,
        text: body.color,
        scheme: body.colorScheme,
      };
    });

    expect(palette).toEqual({
      canvas: "rgb(7, 9, 13)",
      surface: "rgb(23, 27, 35)",
      text: "rgb(245, 247, 250)",
      scheme: "dark",
    });
    await expect(page.locator('meta[name="color-scheme"]')).toHaveAttribute(
      "content",
      "dark",
    );
  });

  test("keeps application names on one line at the startup window size", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "window-940x700", "startup window only");
    await expect(page.getByRole("rowheader", { name: /Signal Collector/ })).toBeVisible();

    const lineCounts = await page.locator(".row-name").evaluateAll((names) =>
      names.map((name) => {
        const style = getComputedStyle(name);
        return name.getBoundingClientRect().height / Number.parseFloat(style.lineHeight);
      }),
    );
    expect(lineCounts.every((count) => count < 1.25)).toBe(true);
  });

  test("fits the complete dashboard in the startup window without page scrolling", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "window-940x700", "startup window only");
    // Edge's 940x700 outer window leaves about this much content after the
    // Windows title bar and borders.
    await page.setViewportSize({ width: 920, height: 650 });
    governor.base = counterReset;
    await page.reload();
    await expect(page.getByRole("rowheader", { name: /Unassigned callers/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Control activity" })).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test("keeps budget detail while compacting application rows at startup", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "window-940x700", "startup window only");
    await expect(page.getByText("Refill rate")).toHaveCount(3);
    await expect(page.getByRole("columnheader", { name: "Recent requests" })).toHaveCount(
      0,
    );
  });

  test("shows brokered REST movement, recency, and the WebSocket measurement boundary", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== "window-940x700", "startup window only");
    await page.setViewportSize({ width: 920, height: 650 });
    await expect(
      page.getByText("Brokered REST activity · WebSockets not measured"),
    ).toBeVisible();
    const row = page.getByRole("row", { name: /Signal Collector/ });
    await expect(row.getByText("12 REST / 1m")).toBeVisible();
    await expect(row.getByText(/^Last REST /)).toBeVisible();
    await expect(
      row.getByRole("img", {
        name: "Signal Collector brokered REST activity: 12 requests over 1m.",
      }),
    ).toBeVisible();

    const next = structuredClone(healthy) as Record<string, any>;
    const hawk = next.apps.find(
      (app: Record<string, unknown>) => app.app_id === "collector",
    );
    hawk.recent_requests = 19;
    hawk.request_series = [0, 1, 0, 4, 2, 12];
    governor.base = next;
    await expect(row.getByText("19 REST / 1m")).toBeVisible({ timeout: 7_000 });
    await expectNoPageOverflow(page);
  });

  test("does not flash an outage for one missed background refresh", async ({ page }) => {
    await expect(page.getByText("ThrottleDeck Broker healthy")).toBeVisible();
    governor.getFailuresRemaining = 1;

    await expect.poll(() => governor.getFailureCount, { timeout: 7_000 }).toBe(1);
    await expect(
      page.getByText(/This window is showing the last good sample/),
    ).toHaveCount(0);
    await expect(page.getByText("ThrottleDeck Broker healthy")).toBeVisible();
  });

  test("keeps every control keyboard reachable with a visible focus ring", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Refresh" }).focus();
    await page.keyboard.press("Tab");
    const stopMoney = page.getByRole("button", { name: "Stop new money orders" });
    await expect(stopMoney).toBeFocused();
    const outline = await stopMoney.evaluate((element) => {
      const style = getComputedStyle(element);
      return { width: style.outlineWidth, style: style.outlineStyle };
    });
    expect(outline.width).toBe("2px");
    expect(outline.style).toBe("solid");
  });

  test("stops one application only after the service confirms, then resumes it", async ({
    page,
  }) => {
    governor.mutationDelayMs = 400;
    const row = page.getByRole("row", { name: /Signal Collector/ });
    await expect(row.getByRole("button", { name: "Stop access" })).toBeVisible();
    await row.getByRole("button", { name: "Stop access" }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveAccessibleName("Stop brokered REST access");
    await expect(dialog.getByText("collector-scanner")).toBeVisible();
    await dialog.getByRole("button", { name: "Stop access" }).click();

    // The window must not move until the service confirms the new revision.
    await expect(row.getByRole("button", { name: "Stop access" })).toBeDisabled();
    await expect(row.getByRole("button", { name: "Resume access" })).toHaveCount(0);

    await expect(row.getByRole("button", { name: "Resume access" })).toBeVisible();
    await expect(dialog).toBeHidden();
    expect(governor.posts[0]?.path).toBe(
      "/api/v1/broker-access/apps/collector/stop",
    );

    await row.getByRole("button", { name: "Resume access" }).click();
    await expect(row.getByRole("button", { name: "Stop access" })).toBeVisible();
    await expect(page.getByText("Allowed").first()).toBeVisible();
  });

  test("never shows a stop as applied when the service refuses it", async ({ page }) => {
    governor.failMutation = true;
    const row = page.getByRole("row", { name: /Signal Collector/ });
    await row.getByRole("button", { name: "Stop access" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Stop access" })
      .click();

    await expect(
      page.getByText("Not applied — Governor did not confirm the change."),
    ).toBeVisible();
    await expect(row.getByRole("button", { name: "Stop access" })).toBeEnabled();
  });

  test("reloads the latest state after a revision conflict", async ({ page }) => {
    const row = page.getByRole("row", { name: /Signal Collector/ });
    // Another window moved the state on while this one was open.
    governor.apps.execution = "stopped";
    governor.revision = 13;
    governor.conflictMutation = true;
    await row.getByRole("button", { name: "Stop access" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Stop access" })
      .click();

    await expect(
      page.getByText("Not applied — Governor did not confirm the change."),
    ).toBeVisible();
    // The reloaded authoritative state wins: the execution bot is stopped; the collector is not.
    await expect(
      page.getByRole("row", { name: /Execution Bot/ }).getByRole("button", {
        name: "Resume access",
      }),
    ).toBeVisible();
    await expect(row.getByRole("button", { name: "Stop access" })).toBeEnabled();
  });

  test("requires the exact typed phrases for the global stop and the money unlock", async ({
    page,
  }) => {
    await page.getByRole("button", { name: "Stop all brokered REST access" }).click();
    const stopAll = page.getByRole("dialog");
    const confirmStopAll = stopAll.getByRole("button", {
      name: "Stop all brokered REST access",
    });
    await expect(confirmStopAll).toBeDisabled();
    await stopAll.getByLabel("Type STOP ALL to confirm").fill("STOP ALL");
    await expect(confirmStopAll).toBeEnabled();
    await stopAll.getByRole("button", { name: "Cancel" }).click();
    await expect(stopAll).toBeHidden();

    governor.tradeLock = "stopped";
    await page.reload();
    await page.getByRole("button", { name: "Unlock" }).click();
    const unlock = page.getByRole("dialog");
    const confirmUnlock = unlock.getByRole("button", { name: "Unlock" });
    await expect(confirmUnlock).toBeDisabled();
    await unlock.getByLabel("Type UNLOCK NEW ORDERS to confirm").fill("UNLOCK NEW ORDERS");
    await expect(confirmUnlock).toBeDisabled();
    await unlock.getByLabel("Reason").fill("Reviewed the direct-caller warning.");
    await expect(confirmUnlock).toBeEnabled();
    await confirmUnlock.click();
    await expect(
      page.getByRole("group", { name: "Execution Bot new money orders" }).getByText("Allowed"),
    ).toBeVisible();
    expect(governor.posts.at(-1)?.body["confirmation"]).toBe("UNLOCK NEW ORDERS");
  });

  test("labels a stale sample with its real age and never as current", async ({ page }) => {
    governor.sampledAt = () => new Date(Date.now() - 240_000).toISOString();
    await page.reload();
    await expect(page.getByText("ThrottleDeck Broker state is stale")).toBeVisible();
    await expect(page.getByText("Updated 4m ago")).toBeVisible();
    await expect(page.getByText("Updated now")).toBeHidden();
  });

  test("fails closed and keeps recovery visible when the broker is down", async ({
    page,
  }) => {
    governor.base = brokerDown;
    governor.brokerState = "down";
    await page.reload();
    await expect(page.getByText("ThrottleDeck Broker is not responding").first()).toBeVisible();
    await expect(
      page.getByText(
        "ThrottleDeck Broker is not responding. New Execution Bot orders remain locked unless Governor can prove otherwise.",
      ),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Unlock" })).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Stop new money orders" }),
    ).toBeEnabled();
    await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();
    await expectNoHorizontalOverflow(page);
  });

  test("states an unconfigured product without inventing a number", async ({ page }) => {
    governor.base = unconfiguredPerps;
    await page.reload();
    await expect(page.getByText("Not configured")).toBeVisible();
    await expect(page.getByText("0 / 0")).toHaveCount(0);
  });

  test("surfaces the unassigned-caller bucket and the direct-caller warning", async ({
    page,
  }) => {
    governor.base = counterReset;
    await page.reload();
    await expect(page.getByRole("rowheader", { name: /Unassigned callers/ })).toBeVisible();
    await expect(page.getByText("Possible direct caller").first()).toBeVisible();
    await expect(
      page.getByText("Polymarket throttled this machine while ThrottleDeck Broker still had room.").first(),
    ).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("opens an application detail with the exact scope of its stop", async ({ page }) => {
    const disclosure = page.getByRole("button", { name: "Execution Bot details" });
    await disclosure.focus();
    await page.keyboard.press("Enter");
    await expect(disclosure).toHaveAttribute("aria-expanded", "true");
    await expect(
      page.getByText("Blocks brokered REST reads for this application's caller labels."),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Does not stop direct venue requests, WebSocket streams, or the application process.",
      ),
    ).toBeVisible();
    await expect(page.getByText("execution-watch-football")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("keeps the money lock visible and operable on a compact window", async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "compact-720x480",
      "compact composition only",
    );
    await expect(
      page.getByRole("button", { name: "Stop new money orders" }),
    ).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expect(page.getByRole("button", { name: "Stop access" }).first()).toBeVisible();
  });

  test("reduces activity to one expandable row on a compact window", async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "compact-720x480",
      "compact composition only",
    );
    const toggle = page.getByRole("button", { name: /Possible direct caller/ });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("Broker started")).toHaveCount(0);
    await toggle.click();
    await expect(page.getByText("Broker started")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("keeps the compact application rows to two lines without extra columns", async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.name !== "compact-720x480",
      "compact composition only",
    );
    await expect(page.getByRole("columnheader", { name: "Recent requests" })).toHaveCount(0);
    const row = page.getByRole("row", { name: /Signal Collector/ });
    await expect(row.getByText("12 REST / 1m")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("shows pressure and queue growth without colour-only status", async ({ page }) => {
    governor.base = warning;
    await page.reload();
    await expect(page.getByText("At limit", { exact: true })).toBeVisible();
    await expect(page.getByText("Pressure", { exact: true })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("still reports a stopped money lock after the window reloads", async ({ page }) => {
    governor.base = stoppedFixture;
    governor.tradeLock = "stopped";
    await page.reload();
    await expect(
      page.getByRole("group", { name: "Execution Bot new money orders" }).getByText("Stopped"),
    ).toBeVisible();
    await expect(
      page
        .getByRole("group", { name: "Execution Bot new money orders" })
        .getByText("Existing orders were not cancelled.", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Execution Bot cannot submit new money orders. Existing orders were not cancelled.",
      ),
    ).toBeVisible();
  });
});
