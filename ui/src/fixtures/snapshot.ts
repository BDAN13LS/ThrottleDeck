/**
 * Typed test fixtures for the versioned snapshot contract.
 *
 * This module is imported only by tests. Production code must never import it:
 * the running dashboard reads every value from `GET /api/v1/snapshot`.
 *
 * The visible numbers are representative, exactly as the approved reference
 * images state; they are not claims about live state.
 */

export type FixtureName =
  | "healthy"
  | "warning"
  | "stopped"
  | "brokerDown"
  | "unconfiguredPerps"
  | "counterReset"
  | "suspectedDirectCaller";

const GENERATED_AT = "2026-09-13T14:24:01.000Z";
const SAMPLED_AT = "2026-09-13T14:24:00.000Z";

function brokerRecord(overrides: Record<string, unknown> = {}) {
  return {
    state: "healthy",
    instance_id: "b3f1c2d4-9a77-4a0e-9c3e-1f7e5a2b6c11",
    started_at: "2026-09-13T10:22:03.000Z",
    sampled_at: SAMPLED_AT,
    sample_age_ms: 1_000,
    tier: "advanced",
    collection_paused: false,
    pause_reason: null,
    ...overrides,
  };
}

function control(
  scope: string,
  state: "allowed" | "stopped",
  revision = 12,
  changedAt: string | null = "2026-09-13T10:22:00.000Z",
) {
  return { scope, state, revision, changed_at: changedAt, reason: null };
}

function budget(
  id: "pmus" | "kalshi.predictions" | "kalshi.perps",
  overrides: Record<string, unknown> = {},
) {
  const pmus = id === "pmus";
  return {
    id,
    venue: pmus ? "pmus" : "kalshi",
    product: pmus ? "rest" : id === "kalshi.predictions" ? "predictions" : "perps",
    configured: true,
    health: "healthy",
    unit: pmus ? "requests" : "tokens",
    available_now: pmus ? 18 : id === "kalshi.predictions" ? 540 : 360,
    capacity: pmus ? 18 : id === "kalshi.predictions" ? 540 : 360,
    refill_per_second: pmus ? 3 : id === "kalshi.predictions" ? 10 : 6,
    queue_depth: 0,
    recent_pressure: 0.12,
    throttled_with_headroom: 0,
    throttled_with_headroom_delta: 0,
    series: pmus
      ? [17, 16, 17, 18, 17, 16, 17, 18, 18, 17, 18, 18]
      : id === "kalshi.predictions"
        ? [520, 526, 531, 540, 536, 528, 533, 540, 540, 534, 538, 540]
        : [352, 348, 355, 360, 357, 350, 354, 360, 358, 355, 359, 360],
    sampled_at: SAMPLED_AT,
    ...overrides,
  };
}

function app(
  appId: string,
  name: string,
  moneyMode: "paper" | "real-money",
  overrides: Record<string, unknown> = {},
) {
  return {
    app_id: appId,
    name,
    money_mode: moneyMode,
    access: "allowed",
    callers: [],
    coverage_note: null,
    recent_requests: 0,
    recent_window_seconds: 60,
    request_series: [0, 0, 0, 0, 0, 0],
    queue_wait_ms: 0,
    errors: 0,
    venue_split: [],
    last_request_at: SAMPLED_AT,
    unassigned: false,
    ...overrides,
  };
}

const COLLECTOR = app("collector", "Signal Collector", "paper", {
  callers: ["collector", "collector-scanner", "collector-us-trades"],
  coverage_note:
    "Scheduled reads are brokered. Scanner WebSocket streams remain direct.",
  recent_requests: 12,
  request_series: [0, 1, 0, 4, 2, 5],
  venue_split: [
    { venue: "polymarket-us", count: 7 },
    { venue: "kalshi", count: 5 },
  ],
});

const EXECUTION = app("execution", "Execution Bot", "real-money", {
  callers: [
    "execution",
    "execution-lag",
    "execution-whales",
    "execution-x",
    "execution-watch-football",
    "execution-reconcile",
    "execution-cockpit",
    "execution-doctor",
  ],
  coverage_note: "Brokered REST reads only. Order submission does not use the broker.",
  recent_requests: 36,
  request_series: [0, 12, 0, 0, 24, 0],
  venue_split: [{ venue: "polymarket-us", count: 36 }],
});

const RESEARCH = app("research", "Market Research", "paper", {
  callers: ["research"],
  coverage_note:
    "Kalshi reads are brokered. Its WebSocket streams remain direct by design.",
  recent_requests: 28,
  request_series: [7, 0, 7, 0, 7, 7],
  queue_wait_ms: 5,
  venue_split: [{ venue: "kalshi", count: 28 }],
});

function activity(
  at: string,
  kind: string,
  severity: "info" | "warning" | "critical",
  detail: string,
) {
  return { at, kind, severity, detail, app_id: null };
}

const ACTIVITY = [
  activity(
    "2026-09-13T14:24:17.000Z",
    "possible-direct-caller",
    "warning",
    "Polymarket throttled this machine while ThrottleDeck Broker still had room.",
  ),
  activity(
    "2026-09-13T14:22:03.000Z",
    "broker-started",
    "info",
    "ThrottleDeck Broker is healthy and accepting requests.",
  ),
  activity(
    "2026-09-13T14:18:31.000Z",
    "access-policy",
    "info",
    "Execution Bot new money orders allowed.",
  ),
  activity(
    "2026-09-13T14:15:12.000Z",
    "application-started",
    "info",
    "Signal Collector connected through ThrottleDeck Broker.",
  ),
  activity(
    "2026-09-13T14:14:01.000Z",
    "application-started",
    "info",
    "Market Research connected through ThrottleDeck Broker.",
  ),
];

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    revision: 12,
    generatedAt: GENERATED_AT,
    broker: brokerRecord(),
    brokerAccess: {
      global: control("broker:global", "allowed"),
      apps: {
        collector: control("broker:app:collector", "allowed"),
        execution: control("broker:app:execution", "allowed"),
        research: control("broker:app:research", "allowed"),
      },
    },
    tradeLock: {
      scope: "trade:new-orders:execution",
      app_id: "execution",
      app_name: "Execution Bot",
      confirmation_phrase: "UNLOCK NEW ORDERS",
      state: "allowed",
      revision: 12,
      changed_at: "2026-09-13T14:18:31.000Z",
    },
    budgets: [
      budget("pmus"),
      budget("kalshi.predictions"),
      budget("kalshi.perps"),
    ],
    apps: [COLLECTOR, EXECUTION, RESEARCH],
    activity: ACTIVITY,
    ...overrides,
  };
}

const UNASSIGNED_ROW = app("unassigned", "Unassigned callers", "paper", {
  callers: [],
  unassigned: true,
  coverage_note:
    "Callers the registry does not know. Their reads are still brokered and still count.",
  recent_requests: 4,
  request_series: [0, 0, 1, 0, 1, 2],
  venue_split: [{ venue: "kalshi", count: 4 }],
});

/** Healthy baseline: every venue readable, every scope allowed. */
export const healthy = snapshot();

/** Amber pressure on one venue and a queue building on another. */
export const warning = snapshot({
  revision: 13,
  budgets: [
    budget("pmus", {
      health: "pressure",
      available_now: 4,
      capacity: 18,
      queue_depth: 3,
      recent_pressure: 0.41,
      series: [18, 16, 12, 9, 7, 6, 5, 5, 4, 4, 4, 4],
    }),
    budget("kalshi.predictions", {
      health: "critical",
      available_now: 12,
      capacity: 540,
      queue_depth: 9,
      recent_pressure: 0.83,
      series: [540, 460, 380, 300, 220, 160, 110, 80, 50, 30, 18, 12],
    }),
    budget("kalshi.perps"),
  ],
});

/** The money lock is committed stopped; broker reads are still allowed. */
export const stopped = snapshot({
  revision: 14,
  tradeLock: {
    scope: "trade:new-orders:execution",
    app_id: "execution",
    app_name: "Execution Bot",
    confirmation_phrase: "UNLOCK NEW ORDERS",
    state: "stopped",
    revision: 14,
    changed_at: "2026-09-13T14:30:02.000Z",
  },
  activity: [
    activity(
      "2026-09-13T14:30:02.000Z",
      "control-change",
      "warning",
      "Execution Bot new money orders stopped by the operator.",
    ),
    ...ACTIVITY,
  ],
});

/** The broker is unreachable; the money lock must fail closed. */
export const brokerDown = snapshot({
  revision: 15,
  broker: brokerRecord({
    state: "down",
    sampled_at: "2026-09-13T14:29:00.000Z",
    sample_age_ms: 61_000,
    instance_id: null,
  }),
  tradeLock: {
    scope: "trade:new-orders:execution",
    app_id: "execution",
    app_name: "Execution Bot",
    confirmation_phrase: "UNLOCK NEW ORDERS",
    state: "unavailable",
    revision: 14,
    changed_at: "2026-09-13T14:30:02.000Z",
  },
  budgets: [
    budget("pmus", {
      health: "unavailable",
      available_now: null,
      capacity: null,
      refill_per_second: null,
      queue_depth: null,
      recent_pressure: null,
      series: [],
      sampled_at: null,
    }),
    budget("kalshi.predictions", {
      health: "unavailable",
      available_now: null,
      capacity: null,
      refill_per_second: null,
      queue_depth: null,
      recent_pressure: null,
      series: [],
      sampled_at: null,
    }),
    budget("kalshi.perps", {
      health: "unavailable",
      available_now: null,
      capacity: null,
      refill_per_second: null,
      queue_depth: null,
      recent_pressure: null,
      series: [],
      sampled_at: null,
    }),
  ],
  activity: [
    activity(
      "2026-09-13T14:29:00.000Z",
      "broker-started",
      "critical",
      "ThrottleDeck Broker stopped answering. The last good sample is 1m old.",
    ),
    ...ACTIVITY.slice(1),
  ],
});

/** Kalshi perps is not configured; nothing is invented to fill the card. */
export const unconfiguredPerps = snapshot({
  budgets: [
    budget("pmus"),
    budget("kalshi.predictions"),
    budget("kalshi.perps", {
      configured: false,
      health: "unconfigured",
      available_now: null,
      capacity: null,
      refill_per_second: null,
      queue_depth: null,
      recent_pressure: null,
      series: [],
    }),
  ],
});

/** A broker restart boundary: cumulative counters reset, deltas do not go negative. */
export const counterReset = snapshot({
  revision: 16,
  broker: brokerRecord({
    instance_id: "5c9a7b21-0f2e-4d55-8b1c-77e0a9d4f302",
    started_at: "2026-09-13T14:20:00.000Z",
  }),
  activity: [
    activity(
      "2026-09-13T14:20:00.000Z",
      "restart",
      "info",
      "ThrottleDeck Broker started again. Counters restart from zero at this point.",
    ),
    ...ACTIVITY,
  ],
  apps: [COLLECTOR, EXECUTION, RESEARCH, UNASSIGNED_ROW],
});

/**
 * A venue throttled this machine while the broker still had headroom, which
 * means some traffic did not travel through the broker.
 */
export const suspectedDirectCaller = snapshot({
  revision: 17,
  budgets: [
    budget("pmus", {
      throttled_with_headroom: 5,
      throttled_with_headroom_delta: 3,
      recent_pressure: 0.28,
    }),
    budget("kalshi.predictions"),
    budget("kalshi.perps"),
  ],
});

export function fixture(name: FixtureName): unknown {
  switch (name) {
    case "healthy":
      return healthy;
    case "warning":
      return warning;
    case "stopped":
      return stopped;
    case "brokerDown":
      return brokerDown;
    case "unconfiguredPerps":
      return unconfiguredPerps;
    case "counterReset":
      return counterReset;
    case "suspectedDirectCaller":
      return suspectedDirectCaller;
  }
}

export const allFixtures: Readonly<Record<FixtureName, unknown>> = {
  healthy,
  warning,
  stopped,
  brokerDown,
  unconfiguredPerps,
  counterReset,
  suspectedDirectCaller,
};
