/**
 * Versioned frontend contract for Governor.
 *
 * The frontend consumes `GET /api/v1/snapshot` (schemaVersion 1) and the
 * explicit action endpoints listed in `controlEndpoint`. Nothing in this file
 * reads a venue, and nothing here fabricates a value the service did not send:
 * a field the service omits becomes `null`, which the UI renders as unknown.
 */

export const SNAPSHOT_SCHEMA_VERSION = 1;

/** Sessions are cookie-bound; mutations must carry the session CSRF token. */
export const CSRF_COOKIE_NAME = "governor_csrf";
export const CSRF_HEADER_NAME = "X-CSRF-Token";

export type AccessState = "allowed" | "stopped";
export type MoneyLockState = "allowed" | "stopped" | "stopping" | "unavailable";
export type BrokerState = "healthy" | "degraded" | "down";
export type BudgetHealth =
  | "healthy"
  | "pressure"
  | "critical"
  | "unavailable"
  | "unconfigured";
export type BudgetUnit = "requests" | "tokens";
export type MoneyMode = "paper" | "real-money";
export type Severity = "info" | "warning" | "critical";

export interface ControlRecord {
  readonly scope: string;
  readonly state: AccessState;
  readonly revision: number;
  readonly changedAt: string | null;
  readonly reason: string | null;
}

export interface MoneyLock {
  readonly scope: string;
  readonly appId: string;
  readonly appName: string;
  readonly confirmationPhrase: string;
  readonly state: MoneyLockState;
  readonly revision: number;
  readonly changedAt: string | null;
}

export interface Budget {
  readonly id: "pmus" | "kalshi.predictions" | "kalshi.perps";
  readonly venue: "pmus" | "kalshi";
  readonly product: "rest" | "predictions" | "perps";
  readonly health: BudgetHealth;
  readonly unit: BudgetUnit;
  readonly availableNow: number | null;
  readonly capacity: number | null;
  readonly refillPerSecond: number | null;
  readonly queueDepth: number | null;
  /** Rolling share of the recent window that hit a limit, 0 to 1. */
  readonly recentPressureRatio: number | null;
  /** Genuine venue throttles observed while the broker still had headroom. */
  readonly throttledWithHeadroom: number | null;
  readonly throttledWithHeadroomDelta: number | null;
  readonly series: readonly number[];
  readonly sampledAt: string | null;
}

export interface VenueSplit {
  readonly venue: string;
  readonly count: number;
  readonly upstream429s: number | null;
  readonly headroom429s: number | null;
  readonly queueWaitMs: number | null;
}

export interface AppRow {
  readonly appId: string;
  readonly name: string;
  readonly moneyMode: MoneyMode;
  readonly access: AccessState;
  readonly callers: readonly string[];
  readonly coverageNote: string | null;
  readonly recentRequests: number | null;
  /** Adjacent broker counter deltas; null marks an unmeasurable interval. */
  readonly requestSeries: readonly (number | null)[];
  readonly recentWindowSeconds: number | null;
  readonly queueWaitMs: number | null;
  readonly errors: number | null;
  readonly venueSplit: readonly VenueSplit[];
  readonly lastRequestAt: string | null;
  /** Callers the registry does not know. Surfaced, never guessed. */
  readonly unassigned: boolean;
}

export interface ActivityEvent {
  readonly at: string;
  readonly kind: string;
  readonly severity: Severity;
  readonly detail: string | null;
  readonly appId: string | null;
}

export interface BrokerRecord {
  readonly state: BrokerState;
  readonly instanceId: string | null;
  readonly startedAt: string | null;
  readonly sampledAt: string | null;
  readonly sampleAgeMs: number | null;
  readonly tier: string | null;
  readonly collectionPaused: boolean;
  readonly pauseReason: string | null;
}

export interface BrokerAccess {
  readonly global: ControlRecord;
  readonly apps: Readonly<Record<string, ControlRecord>>;
}

export interface GovernorSnapshot {
  readonly schemaVersion: number;
  readonly revision: number;
  readonly generatedAt: string;
  readonly broker: BrokerRecord;
  readonly brokerAccess: BrokerAccess;
  readonly tradeLock: MoneyLock;
  readonly budgets: readonly Budget[];
  readonly apps: readonly AppRow[];
  readonly activity: readonly ActivityEvent[];
}

export type SnapshotProblemKind =
  | "unsupported-schema"
  | "invalid-payload"
  | "unreachable";

export interface SnapshotProblem {
  readonly kind: SnapshotProblemKind;
  readonly message: string;
}

export type SnapshotParse =
  | { readonly ok: true; readonly snapshot: GovernorSnapshot }
  | { readonly ok: false; readonly problem: SnapshotProblem };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

function readArray(value: unknown): readonly unknown[] {
  return Array.isArray(value) ? value : [];
}

function readStringArray(value: unknown): readonly string[] {
  return readArray(value).filter((item): item is string => typeof item === "string");
}

function readAccessState(value: unknown): AccessState {
  return value === "stopped" ? "stopped" : "allowed";
}

function readMoneyLockState(value: unknown): MoneyLockState {
  if (value === "stopped" || value === "stopping" || value === "unavailable") {
    return value;
  }
  return "allowed";
}

function readControlRecord(value: unknown, fallbackScope: string): ControlRecord {
  const record = isRecord(value) ? value : {};
  return {
    scope: readString(record["scope"]) ?? fallbackScope,
    state: readAccessState(record["state"]),
    revision: readNumber(record["revision"]) ?? 0,
    changedAt: readString(record["changed_at"] ?? record["changedAt"]),
    reason: readString(record["reason"]),
  };
}

const BUDGET_IDS: readonly Budget["id"][] = [
  "pmus",
  "kalshi.predictions",
  "kalshi.perps",
];

function emptyBudget(id: Budget["id"]): Budget {
  const venue: Budget["venue"] = id === "pmus" ? "pmus" : "kalshi";
  const product: Budget["product"] =
    id === "pmus" ? "rest" : id === "kalshi.predictions" ? "predictions" : "perps";
  return {
    id,
    venue,
    product,
    health: "unavailable",
    unit: venue === "pmus" ? "requests" : "tokens",
    availableNow: null,
    capacity: null,
    refillPerSecond: null,
    queueDepth: null,
    recentPressureRatio: null,
    throttledWithHeadroom: null,
    throttledWithHeadroomDelta: null,
    series: [],
    sampledAt: null,
  };
}

function readBudgetHealth(value: unknown): BudgetHealth {
  if (
    value === "healthy" ||
    value === "pressure" ||
    value === "critical" ||
    value === "unconfigured"
  ) {
    return value;
  }
  return "unavailable";
}

function readBudget(value: unknown, id: Budget["id"]): Budget {
  if (!isRecord(value)) {
    return emptyBudget(id);
  }
  const base = emptyBudget(id);
  const configured = value["configured"];
  return {
    ...base,
    health:
      configured === false ? "unconfigured" : readBudgetHealth(value["health"]),
    availableNow: readNumber(value["available_now"] ?? value["availableNow"]),
    capacity: readNumber(value["capacity"]),
    refillPerSecond: readNumber(
      value["refill_per_second"] ?? value["refillPerSecond"],
    ),
    queueDepth: readNumber(value["queue_depth"] ?? value["queueDepth"]),
    recentPressureRatio: readNumber(
      value["recent_pressure"] ?? value["recentPressureRatio"],
    ),
    throttledWithHeadroom: readNumber(
      value["throttled_with_headroom"] ?? value["throttledWithHeadroom"],
    ),
    throttledWithHeadroomDelta: readNumber(
      value["throttled_with_headroom_delta"] ??
        value["throttledWithHeadroomDelta"],
    ),
    series: readArray(value["series"]).map((point) => readNumber(point) ?? 0),
    sampledAt: readString(value["sampled_at"] ?? value["sampledAt"]),
  };
}

function readMoneyMode(value: unknown): MoneyMode {
  return value === "real-money" ? "real-money" : "paper";
}

function readAppRow(value: unknown): AppRow | null {
  if (!isRecord(value)) {
    return null;
  }
  const appId = readString(value["app_id"] ?? value["appId"]);
  const name = readString(value["name"]);
  if (appId === null || name === null) {
    return null;
  }
  const windowSeconds = readNumber(
    value["recent_window_seconds"] ?? value["recentWindowSeconds"],
  );
  return {
    appId,
    name,
    moneyMode: readMoneyMode(value["money_mode"] ?? value["moneyMode"]),
    access: readAccessState(value["access"]),
    callers: readStringArray(value["callers"]),
    coverageNote: readString(value["coverage_note"] ?? value["coverageNote"]),
    recentRequests: readNumber(
      value["recent_requests"] ?? value["recentRequests"],
    ),
    requestSeries: readArray(
      value["request_series"] ?? value["requestSeries"],
    ).map((point) => {
      if (point === null) {
        return null;
      }
      const parsed = readNumber(point);
      return parsed === null ? null : Math.max(0, Math.round(parsed));
    }),
    recentWindowSeconds: windowSeconds,
    queueWaitMs: readNumber(value["queue_wait_ms"] ?? value["queueWaitMs"]),
    errors: readNumber(value["errors"]),
    venueSplit: readArray(value["venue_split"] ?? value["venueSplit"])
      .map((item) => {
        if (!isRecord(item)) {
          return null;
        }
        const venue = readString(item["venue"]);
        return venue === null
          ? null
          : {
              venue,
              count: readNumber(item["count"]) ?? 0,
              upstream429s: readNumber(item["upstream_429s"] ?? item["upstream429s"]),
              headroom429s: readNumber(item["headroom_429s"] ?? item["headroom429s"]),
              queueWaitMs: readNumber(item["queue_wait_ms"] ?? item["queueWaitMs"]),
            };
      })
      .filter((item): item is VenueSplit => item !== null),
    lastRequestAt: readString(value["last_request_at"] ?? value["lastRequestAt"]),
    unassigned: readBoolean(value["unassigned"]),
  };
}

function readSeverity(value: unknown): Severity {
  if (value === "warning" || value === "critical") {
    return value;
  }
  return "info";
}

function readActivityEvent(value: unknown): ActivityEvent | null {
  if (!isRecord(value)) {
    return null;
  }
  const at = readString(value["at"]);
  const kind = readString(value["kind"]);
  if (at === null || kind === null) {
    return null;
  }
  return {
    at,
    kind,
    severity: readSeverity(value["severity"]),
    detail: readString(value["detail"]),
    appId: readString(value["app_id"] ?? value["appId"]),
  };
}

function readBrokerRecord(value: unknown): BrokerRecord {
  const record = isRecord(value) ? value : {};
  const state = record["state"];
  return {
    state: state === "down" || state === "degraded" ? state : "healthy",
    instanceId: readString(record["instance_id"] ?? record["instanceId"]),
    startedAt: readString(record["started_at"] ?? record["startedAt"]),
    sampledAt: readString(record["sampled_at"] ?? record["sampledAt"]),
    sampleAgeMs: readNumber(record["sample_age_ms"] ?? record["sampleAgeMs"]),
    tier: readString(record["tier"]),
    collectionPaused: readBoolean(
      record["collection_paused"] ?? record["collectionPaused"],
    ),
    pauseReason: readString(record["pause_reason"] ?? record["pauseReason"]),
  };
}

/**
 * Validate a snapshot payload. An unknown `schemaVersion` is surfaced as a
 * problem rather than parsed on hope; a malformed payload is rejected instead
 * of being rendered as an incomplete truth.
 */
export function parseSnapshot(payload: unknown): SnapshotParse {
  if (!isRecord(payload)) {
    return {
      ok: false,
      problem: {
        kind: "invalid-payload",
        message: "Governor sent a snapshot the interface could not read.",
      },
    };
  }
  const schemaVersion = payload["schemaVersion"];
  if (typeof schemaVersion !== "number") {
    return {
      ok: false,
      problem: {
        kind: "invalid-payload",
        message: "Governor sent a snapshot without a schema version.",
      },
    };
  }
  if (schemaVersion !== SNAPSHOT_SCHEMA_VERSION) {
    return {
      ok: false,
      problem: {
        kind: "unsupported-schema",
        message: `Unsupported snapshot version ${schemaVersion}. This window understands version ${SNAPSHOT_SCHEMA_VERSION}.`,
      },
    };
  }

  const revision = readNumber(payload["revision"]);
  const generatedAt = readString(payload["generatedAt"] ?? payload["generated_at"]);
  const tradeLock = isRecord(payload["tradeLock"] ?? payload["trade_lock"])
    ? ((payload["tradeLock"] ?? payload["trade_lock"]) as Record<string, unknown>)
    : {};
  const brokerAccessRaw = payload["brokerAccess"] ?? payload["broker_access"];
  if (
    revision === null ||
    generatedAt === null ||
    !isRecord(brokerAccessRaw) ||
    !isRecord(payload["broker"])
  ) {
    return {
      ok: false,
      problem: {
        kind: "invalid-payload",
        message: "Governor sent a snapshot with missing required fields.",
      },
    };
  }

  const budgetsRaw = readArray(payload["budgets"]);
  const budgets = BUDGET_IDS.map((id) => {
    const match = budgetsRaw.find(
      (item) => isRecord(item) && (item["id"] === id || item["budget"] === id),
    );
    return readBudget(match, id);
  });

  const apps = readArray(payload["apps"])
    .map(readAppRow)
    .filter((row): row is AppRow => row !== null);

  const activity = readArray(payload["activity"])
    .map(readActivityEvent)
    .filter((event): event is ActivityEvent => event !== null);

  const appsAccess: Record<string, ControlRecord> = {};
  const appsAccessRaw = brokerAccessRaw["apps"];
  if (isRecord(appsAccessRaw)) {
    for (const [appId, record] of Object.entries(appsAccessRaw)) {
      appsAccess[appId] = readControlRecord(record, `broker:app:${appId}`);
    }
  }

  return {
    ok: true,
    snapshot: {
      schemaVersion,
      revision,
      generatedAt,
      broker: readBrokerRecord(payload["broker"]),
      brokerAccess: {
        global: readControlRecord(brokerAccessRaw["global"], "broker:global"),
        apps: appsAccess,
      },
      tradeLock: {
        scope:
          readString(tradeLock["scope"]) ?? "trade:new-orders:execution",
        appId: readString(tradeLock["app_id"] ?? tradeLock["appId"]) ?? "execution",
        appName: readString(tradeLock["app_name"] ?? tradeLock["appName"]) ?? "Protected app",
        confirmationPhrase:
          readString(
            tradeLock["confirmation_phrase"] ?? tradeLock["confirmationPhrase"],
          ) ?? "UNLOCK NEW ORDERS",
        state: readMoneyLockState(tradeLock["state"]),
        revision: readNumber(tradeLock["revision"]) ?? 0,
        changedAt: readString(tradeLock["changed_at"] ?? tradeLock["changedAt"]),
      },
      budgets,
      apps,
      activity,
    },
  };
}

export interface ControlAction {
  readonly kind: "broker-access" | "trade-lock";
  readonly action: "stop" | "resume" | "unlock";
  /** `null` means the machine-wide broker-access scope. */
  readonly appId: string | null;
  readonly endpoint: string;
  readonly expectedRevision: number;
  readonly reason: string | null;
  readonly confirmation: string | null;
}

export type ControlResultStatus = "applied" | "conflict" | "failed";

export interface ControlResult {
  readonly status: ControlResultStatus;
  /** The authoritative committed snapshot, or the reloaded one after a conflict. */
  readonly snapshot: GovernorSnapshot | null;
  readonly message: string | null;
}
