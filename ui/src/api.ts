/**
 * The only module that talks to Governor.
 *
 * It calls loopback, same-origin endpoints with the session cookie and the
 * session CSRF token. It never falls back to a venue call, and it never invents
 * state: a control result is authoritative only when the service returns a
 * committed snapshot.
 */

import {
  CSRF_COOKIE_NAME,
  CSRF_HEADER_NAME,
  parseSnapshot,
  type ControlAction,
  type ControlResult,
  type GovernorSnapshot,
  type SnapshotProblem,
} from "./contracts";
import { COPY } from "./derive";

export const SNAPSHOT_URL = "/api/v1/snapshot";

export type SnapshotLoad =
  | { readonly ok: true; readonly snapshot: GovernorSnapshot }
  | { readonly ok: false; readonly problem: SnapshotProblem };

export type Fetcher = (
  input: string,
  init?: RequestInit,
) => Promise<Response>;

function readCookie(name: string): string | null {
  if (typeof document === "undefined") {
    return null;
  }
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

/**
 * The session CSRF token is published as a readable cookie so a static bundle
 * can echo it back in a header. A meta tag is accepted as a fallback for a
 * server that injects the token into `index.html` instead.
 */
export function readCsrfToken(): string | null {
  const fromCookie = readCookie(CSRF_COOKIE_NAME);
  if (fromCookie !== null && fromCookie.length > 0) {
    return fromCookie;
  }
  if (typeof document === "undefined") {
    return null;
  }
  const meta = document.querySelector<HTMLMetaElement>(
    'meta[name="governor-csrf"]',
  );
  const content = meta?.content ?? "";
  return content.length > 0 ? content : null;
}

function jsonHeaders(): HeadersInit {
  return { Accept: "application/json" };
}

export async function loadSnapshot(
  fetcher: Fetcher = globalThis.fetch,
): Promise<SnapshotLoad> {
  let response: Response;
  try {
    response = await fetcher(SNAPSHOT_URL, {
      method: "GET",
      headers: jsonHeaders(),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    return {
      ok: false,
      problem: {
        kind: "unreachable",
        message: "Governor is not responding.",
      },
    };
  }

  if (!response.ok) {
    return {
      ok: false,
      problem: {
        kind: response.status >= 500 ? "unreachable" : "invalid-payload",
        message:
          response.status >= 500
            ? "Governor is not responding."
            : `Governor refused the snapshot request with status ${response.status}.`,
      },
    };
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return {
      ok: false,
      problem: {
        kind: "invalid-payload",
        message: "Governor sent a snapshot the interface could not read.",
      },
    };
  }
  return parseSnapshot(payload);
}

function actionBody(action: ControlAction): Record<string, unknown> {
  const body: Record<string, unknown> = {
    expected_revision: action.expectedRevision,
  };
  if (action.reason !== null) {
    body["reason"] = action.reason;
  }
  if (action.confirmation !== null) {
    body["confirmation"] = action.confirmation;
  }
  return body;
}

function snapshotFromResponseBody(body: unknown): SnapshotLoad {
  const candidate =
    typeof body === "object" && body !== null && !Array.isArray(body)
      ? ((body as Record<string, unknown>)["snapshot"] ?? body)
      : body;
  return parseSnapshot(candidate);
}

/**
 * Submit one control action. The caller must not change any visible state until
 * this resolves with `status: "applied"`.
 */
export async function applyControl(
  action: ControlAction,
  fetcher: Fetcher = globalThis.fetch,
): Promise<ControlResult> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(jsonHeaders() as Record<string, string>),
  };
  const csrf = readCsrfToken();
  if (csrf !== null) {
    headers[CSRF_HEADER_NAME] = csrf;
  }

  let response: Response;
  try {
    response = await fetcher(action.endpoint, {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: JSON.stringify(actionBody(action)),
    });
  } catch {
    return { status: "failed", snapshot: null, message: COPY.notApplied };
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (response.status === 409) {
    const parsed = snapshotFromResponseBody(payload);
    return {
      status: "conflict",
      snapshot: parsed.ok ? parsed.snapshot : null,
      message: COPY.notApplied,
    };
  }

  if (!response.ok) {
    return { status: "failed", snapshot: null, message: COPY.notApplied };
  }

  const parsed = snapshotFromResponseBody(payload);
  if (!parsed.ok) {
    return { status: "failed", snapshot: null, message: COPY.notApplied };
  }
  return { status: "applied", snapshot: parsed.snapshot, message: null };
}

export function stopBrokerAccess(
  snapshot: GovernorSnapshot,
  appId: string | null,
  reason: string,
): ControlAction {
  return {
    kind: "broker-access",
    action: "stop",
    appId,
    endpoint:
      appId === null
        ? "/api/v1/broker-access/global/stop"
        : `/api/v1/broker-access/apps/${encodeURIComponent(appId)}/stop`,
    expectedRevision: snapshot.revision,
    reason,
    confirmation: appId === null ? "STOP ALL" : null,
  };
}

export function resumeBrokerAccess(
  snapshot: GovernorSnapshot,
  appId: string | null,
): ControlAction {
  return {
    kind: "broker-access",
    action: "resume",
    appId,
    endpoint:
      appId === null
        ? "/api/v1/broker-access/global/resume"
        : `/api/v1/broker-access/apps/${encodeURIComponent(appId)}/resume`,
    expectedRevision: snapshot.revision,
    reason: "Operator resumed brokered REST access from Governor.",
    confirmation: null,
  };
}

export function stopNewMoneyOrders(snapshot: GovernorSnapshot): ControlAction {
  return {
    kind: "trade-lock",
    action: "stop",
    appId: snapshot.tradeLock.appId,
    endpoint: "/api/v1/trade-lock/execution/stop",
    expectedRevision: snapshot.revision,
    reason: `Operator stopped new ${snapshot.tradeLock.appName} money orders from Governor.`,
    confirmation: null,
  };
}

export function unlockNewMoneyOrders(
  snapshot: GovernorSnapshot,
  reason: string,
): ControlAction {
  return {
    kind: "trade-lock",
    action: "unlock",
    appId: snapshot.tradeLock.appId,
    endpoint: "/api/v1/trade-lock/execution/unlock",
    expectedRevision: snapshot.revision,
    reason,
    confirmation: snapshot.tradeLock.confirmationPhrase,
  };
}
