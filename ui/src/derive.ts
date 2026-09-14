/**
 * Pure derivations that turn a validated snapshot into the exact copy and
 * measurements the design contract allows.
 *
 * Rules encoded here:
 * - A number the service did not send is shown as unknown, never as zero.
 * - `Available now` is the current local bucket balance, never a monthly quota.
 * - A stale sample is labelled with its real age and is never shown as current.
 * - Text and shape always carry the state; colour never carries it alone.
 */

import type {
  ActivityEvent,
  AppRow,
  Budget,
  GovernorSnapshot,
  MoneyLock,
  MoneyLockState,
  Severity,
} from "./contracts";

/** A sample older than three missed five-second polls is stale. */
export const STALE_AFTER_MS = 15_000;

export const COPY = {
  product: "ThrottleDeck",
  descriptor: "API Control",
  refresh: "Refresh",
  updatedNow: "Updated now",
  healthy: "Healthy",
  brokerHealthy: "ThrottleDeck Broker healthy",
  brokerNotResponding: "ThrottleDeck Broker is not responding",
  brokerUpdateRequired: "ThrottleDeck Broker update required",
  brokerStale: "ThrottleDeck Broker state is stale",
  allowed: "Allowed",
  stopped: "Stopped",
  stopping: "Stopping",
  unavailable: "Unavailable",
  notConfigured: "Not configured",
  availableNow: "Available now",
  refillRate: "Refill rate",
  queueDepth: "Queue depth",
  recentPressure: "Recent pressure",
  accountQuotaUnknown: "Account quota unknown",
  applications: "Applications",
  name: "Name",
  brokeredRest: "Brokered REST",
  recentRequests: "Recent requests",
  queueWait: "Queue wait",
  errors: "Errors",
  access: "Access",
  yes: "Yes",
  paper: "Paper",
  realMoney: "Real money",
  stopAccess: "Stop access",
  resumeAccess: "Resume access",
  stopAllAccess: "Stop all brokered REST access",
  resumeAllAccess: "Resume all brokered REST access",
  stopNewMoneyOrders: "Stop new money orders",
  unlock: "Unlock",
  recentActivity: "Control activity",
  possibleDirectCaller: "Possible direct caller",
  directCallerDetail:
    "Polymarket throttled this machine while ThrottleDeck Broker still had room.",
  notApplied: "Not applied — Governor did not confirm the change.",
  unknownValue: "Unknown",
} as const;

export type Tone = "healthy" | "warning" | "critical" | "stop" | "neutral";

export function budgetTitle(budget: Budget): string {
  switch (budget.id) {
    case "pmus":
      return "Polymarket REST";
    case "kalshi.predictions":
      return "Kalshi Predictions";
    case "kalshi.perps":
      return "Kalshi Perps";
  }
}

export function budgetStatusLabel(budget: Budget): string {
  switch (budget.health) {
    case "healthy":
      return COPY.healthy;
    case "pressure":
      return "Pressure";
    case "critical":
      return "At limit";
    case "unconfigured":
      return COPY.notConfigured;
    case "unavailable":
      return COPY.unavailable;
  }
}

export function budgetTone(budget: Budget): Tone {
  switch (budget.health) {
    case "healthy":
      return "healthy";
    case "pressure":
      return "warning";
    case "critical":
      return "critical";
    case "unconfigured":
    case "unavailable":
      return "neutral";
  }
}

export function isBudgetReadable(budget: Budget): boolean {
  return budget.health !== "unavailable" && budget.health !== "unconfigured";
}

/** `Available now` is the local bucket balance; a missing balance is unknown. */
export function formatAvailable(budget: Budget): string {
  if (!isBudgetReadable(budget)) {
    return COPY.unavailable;
  }
  if (budget.availableNow === null) {
    return COPY.unknownValue;
  }
  if (budget.capacity === null) {
    return `${budget.availableNow}`;
  }
  return `${budget.availableNow} / ${budget.capacity}`;
}

export function budgetUnitLabel(budget: Budget): string {
  return budget.unit;
}

export function formatRefill(budget: Budget): string {
  if (!isBudgetReadable(budget)) {
    return COPY.unavailable;
  }
  if (budget.refillPerSecond === null) {
    return COPY.accountQuotaUnknown;
  }
  const unit = budget.unit === "requests" ? "req/s" : "tokens/s";
  return `${trimNumber(budget.refillPerSecond)} ${unit}`;
}

export function formatQueueDepth(budget: Budget): string {
  if (!isBudgetReadable(budget) || budget.queueDepth === null) {
    return COPY.unknownValue;
  }
  return `${budget.queueDepth}`;
}

export function formatBudgetPressure(budget: Budget): string {
  if (!isBudgetReadable(budget) || budget.recentPressureRatio === null) {
    return COPY.unknownValue;
  }
  return `${Math.round(budget.recentPressureRatio * 100)}%`;
}

function trimNumber(value: number): string {
  return Number.isInteger(value) ? `${value}` : value.toFixed(1);
}

export function appMoneyLabel(app: AppRow): string {
  return app.moneyMode === "real-money" ? COPY.realMoney : COPY.paper;
}

export function appAccessLabel(app: AppRow): string {
  return app.access === "stopped" ? COPY.stopped : COPY.allowed;
}

export function formatRecentRequests(app: AppRow): string {
  if (app.recentRequests === null) {
    return "REST traffic unavailable";
  }
  const window = app.recentWindowSeconds;
  if (window === null) {
    return `${app.recentRequests} REST`;
  }
  return `${app.recentRequests} REST / ${formatWindow(window)}`;
}

function formatWindow(seconds: number): string {
  if (seconds % 60 === 0) {
    return `${seconds / 60}m`;
  }
  return `${seconds}s`;
}

export function formatLastRest(app: AppRow, now: number): string {
  const age = ageMs(app.lastRequestAt, now);
  if (age === null) {
    return "No REST recorded";
  }
  if (age < 5_000) {
    return "Last REST now";
  }
  if (age < 60_000) {
    return `Last REST ${Math.round(age / 1_000)}s ago`;
  }
  if (age < 3_600_000) {
    return `Last REST ${Math.floor(age / 60_000)}m ago`;
  }
  if (age < 86_400_000) {
    return `Last REST ${Math.floor(age / 3_600_000)}h ago`;
  }
  return `Last REST ${Math.floor(age / 86_400_000)}d ago`;
}

export function describeRequestSeries(app: AppRow): string {
  if (app.recentRequests === null || app.recentWindowSeconds === null) {
    return `${app.name} brokered REST activity is unavailable.`;
  }
  return `${app.name} brokered REST activity: ${app.recentRequests} requests over ${formatWindow(app.recentWindowSeconds)}.`;
}

export function formatQueueWait(app: AppRow): string {
  if (app.queueWaitMs === null) {
    return COPY.unknownValue;
  }
  return `${Math.round(app.queueWaitMs)} ms`;
}

export function formatErrors(app: AppRow): string {
  return app.errors === null ? COPY.unknownValue : `${app.errors}`;
}

export function formatBrokeredRest(app: AppRow): string {
  return app.access === "stopped" ? COPY.stopped : COPY.yes;
}

export function ageMs(
  sampledAt: string | null | undefined,
  now: number,
): number | null {
  if (!sampledAt) {
    return null;
  }
  const parsed = Date.parse(sampledAt);
  if (Number.isNaN(parsed)) {
    return null;
  }
  return Math.max(0, now - parsed);
}

export function formatAge(sampledAt: string | null | undefined, now: number): string {
  const age = ageMs(sampledAt, now);
  if (age === null) {
    return COPY.unknownValue;
  }
  if (age < 5_000) {
    return COPY.updatedNow;
  }
  if (age < 60_000) {
    return `Updated ${Math.round(age / 1_000)}s ago`;
  }
  return `Updated ${Math.floor(age / 60_000)}m ago`;
}

export function snapshotSampleAgeMs(
  snapshot: GovernorSnapshot,
  now: number,
): number | null {
  const fromBroker = ageMs(snapshot.broker.sampledAt, now);
  if (fromBroker !== null) {
    return fromBroker;
  }
  return ageMs(snapshot.generatedAt, now);
}

export function isSnapshotStale(snapshot: GovernorSnapshot, now: number): boolean {
  if (snapshot.broker.state === "down") {
    return false;
  }
  const age = snapshotSampleAgeMs(snapshot, now);
  return age !== null && age > STALE_AFTER_MS;
}

export function brokerHealthLabel(
  snapshot: GovernorSnapshot,
  now: number,
): string {
  if (snapshot.broker.state === "down") {
    return COPY.brokerNotResponding;
  }
  if (snapshot.broker.state === "degraded") {
    return COPY.brokerUpdateRequired;
  }
  if (isSnapshotStale(snapshot, now)) {
    return COPY.brokerStale;
  }
  return COPY.brokerHealthy;
}

export function brokerHealthTone(
  snapshot: GovernorSnapshot,
  now: number,
): Tone {
  if (snapshot.broker.state === "down") {
    return "critical";
  }
  if (snapshot.broker.state === "degraded") {
    return "warning";
  }
  return isSnapshotStale(snapshot, now) ? "warning" : "healthy";
}

export interface MoneyLockView {
  readonly heading: string;
  readonly stateLabel: string;
  readonly detail: string;
  readonly banner: string | null;
  readonly actionLabel: string;
  readonly tone: Tone;
  readonly canStop: boolean;
  readonly canUnlock: boolean;
}

export function moneyLockView(lock: MoneyLock): MoneyLockView {
  const heading = `${lock.appName} new money orders`;
  switch (lock.state) {
    case "allowed":
      return {
        heading,
        stateLabel: COPY.allowed,
        detail: `${lock.appName} may submit new real-money orders.`,
        banner: null,
        actionLabel: COPY.stopNewMoneyOrders,
        tone: "healthy",
        canStop: true,
        canUnlock: false,
      };
    case "stopped":
      return {
        heading,
        stateLabel: COPY.stopped,
        detail: "Existing orders were not cancelled.",
        banner:
          `${lock.appName} cannot submit new money orders. Existing orders were not cancelled.`,
        actionLabel: COPY.unlock,
        tone: "stop",
        canStop: false,
        canUnlock: true,
      };
    case "stopping":
      return {
        heading,
        stateLabel: COPY.stopping,
        detail: "An order decision is still resolving. Stopping again is safe.",
        banner:
          `${lock.appName} cannot submit new money orders. Existing orders were not cancelled.`,
        actionLabel: COPY.stopNewMoneyOrders,
        tone: "warning",
        canStop: true,
        canUnlock: false,
      };
    case "unavailable":
      return {
        heading,
        stateLabel: COPY.unavailable,
        detail:
          `ThrottleDeck Broker is not responding. New ${lock.appName} orders remain locked unless Governor can prove otherwise.`,
        banner:
          `New ${lock.appName} orders remain locked while Governor cannot prove the lock is unlocked.`,
        actionLabel: COPY.stopNewMoneyOrders,
        tone: "critical",
        canStop: true,
        canUnlock: false,
      };
  }
}

export const CONTROL_EVENT_TITLES: Readonly<Record<string, string>> = {
  "broker-started": "Broker started",
  "access-policy": "Access policy",
  "application-started": "Application started",
  "possible-direct-caller": COPY.possibleDirectCaller,
  "control-change": "Access policy",
  "storage-paused": "History paused",
  "restart": "Broker started",
  "broker-update-required": "Broker update required",
};

/** Recognised kinds get the approved copy; an unknown kind is surfaced. */
export function eventTitle(event: ActivityEvent): string {
  const known = CONTROL_EVENT_TITLES[event.kind];
  if (known !== undefined) {
    return known;
  }
  return event.kind;
}

export function isHighPriorityEvent(event: ActivityEvent): boolean {
  return event.severity === "warning" || event.severity === "critical";
}

export function severityTone(severity: Severity): Tone {
  switch (severity) {
    case "critical":
      return "critical";
    case "warning":
      return "warning";
    case "info":
      return "healthy";
  }
}

/**
 * A direct caller is suspected when a genuine venue throttle happened while
 * the broker still had headroom: the request did not come from here.
 */
export function hasHeadroomDeltaAlert(snapshot: GovernorSnapshot): boolean {
  return snapshot.budgets.some(
    (budget) =>
      budget.throttledWithHeadroomDelta !== null &&
      budget.throttledWithHeadroomDelta > 0,
  );
}

export function possibleDirectCallerEvent(
  snapshot: GovernorSnapshot,
): ActivityEvent | null {
  const flagged = snapshot.activity.find(
    (event) =>
      event.kind === "possible-direct-caller" &&
      isHighPriorityEvent(event),
  );
  return flagged ?? null;
}

export function directCallerAlert(snapshot: GovernorSnapshot): boolean {
  return (
    hasHeadroomDeltaAlert(snapshot) || possibleDirectCallerEvent(snapshot) !== null
  );
}

export function latestHighPriorityEvent(
  snapshot: GovernorSnapshot,
): ActivityEvent | null {
  const possible = possibleDirectCallerEvent(snapshot);
  if (possible !== null) {
    return possible;
  }
  return snapshot.activity.find(isHighPriorityEvent) ?? null;
}

export function describeSeries(budget: Budget): string {
  const title = budgetTitle(budget);
  if (!isBudgetReadable(budget) || budget.series.length === 0) {
    return `${title}: no recent sample available.`;
  }
  const first = budget.series[0] ?? 0;
  const last = budget.series[budget.series.length - 1] ?? 0;
  const unit = budgetUnitLabel(budget);
  return `${title}: ${first} to ${last} ${unit} available across the last ${budget.series.length} samples.`;
}

export function formatClock(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return COPY.unknownValue;
  }
  const pad = (value: number): string => `${value}`.padStart(2, "0");
  return `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`;
}

export function globalAccessLabel(snapshot: GovernorSnapshot): string {
  return snapshot.brokerAccess.global.state === "stopped"
    ? COPY.stopped
    : COPY.allowed;
}

export function appAccessRecord(
  snapshot: GovernorSnapshot,
  app: AppRow,
): "allowed" | "stopped" {
  return snapshot.brokerAccess.apps[app.appId]?.state ?? app.access;
}

export interface AppDetailView {
  readonly title: string;
  readonly moneyLine: string;
  readonly brokeredRestLine: string;
  readonly doesLine: string;
  readonly doesNotLine: string;
  readonly coverage: string | null;
}

/**
 * The exact scope of one application's stop. The statement separates brokered
 * reads from direct traffic, WebSocket streams, and the process itself, because
 * a broker-access stop does not touch any of those.
 */
export function appDetailView(app: AppRow, lock: MoneyLock): AppDetailView {
  const stopped = app.access === "stopped";
  const lockWord: Record<MoneyLockState, string> = {
    allowed: "allowed",
    stopped: "stopped",
    stopping: "stopping",
    unavailable: "unavailable",
  };
  return {
    title: app.name,
    moneyLine:
      app.appId === lock.appId
        ? `Real money: new order submission is gated by the ${lock.appName} money lock, which is currently ${lockWord[lock.state]}. Brokered REST reads are separate.`
        : app.moneyMode === "real-money"
          ? "Real money: no Governor new-order gate is configured for this application."
        : "Paper: this application does not submit real-money orders.",
    brokeredRestLine: stopped
      ? "Brokered REST reads are stopped for these caller labels."
      : "Brokered REST reads are allowed for these caller labels.",
    doesLine: "Blocks brokered REST reads for this application's caller labels.",
    doesNotLine:
      "Does not stop direct venue requests, WebSocket streams, or the application process.",
    coverage: app.coverageNote,
  };
}
