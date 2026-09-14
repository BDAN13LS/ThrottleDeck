import { useCallback, useEffect, useRef, useState } from "react";

import {
  applyControl,
  loadSnapshot,
  resumeBrokerAccess,
  stopBrokerAccess,
  stopNewMoneyOrders,
  unlockNewMoneyOrders,
  type Fetcher,
} from "./api";
import { ActivityFeed } from "./components/ActivityFeed";
import { AppTable } from "./components/AppTable";
import { BudgetCard } from "./components/BudgetCard";
import { ConfirmDialog, type ConfirmRequest } from "./components/ConfirmDialog";
import { Header } from "./components/Header";
import { SafetyStrip } from "./components/SafetyStrip";
import { RefreshIcon } from "./components/icons";
import type {
  AppRow,
  ControlAction,
  GovernorSnapshot,
  SnapshotProblem,
} from "./contracts";
import { COPY } from "./derive";

export interface AppProps {
  /** Injected by tests only; production always uses the browser fetch. */
  readonly fetcher?: Fetcher;
  readonly pollMs?: number;
  readonly clock?: () => number;
}

export function App({ fetcher, pollMs = 5_000, clock = Date.now }: AppProps) {
  const [snapshot, setSnapshot] = useState<GovernorSnapshot | null>(null);
  const [problem, setProblem] = useState<SnapshotProblem | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [pending, setPending] = useState(false);
  const [controlError, setControlError] = useState<string | null>(null);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  const [detailAppId, setDetailAppId] = useState<string | null>(null);
  const snapshotRef = useRef<GovernorSnapshot | null>(null);
  const automaticFailuresRef = useRef(0);
  const latestLoadRef = useRef(0);
  const now = useNow(clock);

  const load = useCallback(
    async (manual: boolean) => {
      const loadId = latestLoadRef.current + 1;
      latestLoadRef.current = loadId;
      if (manual) {
        setRefreshing(true);
      }
      const result = await loadSnapshot(fetcher);
      if (manual) {
        setRefreshing(false);
      }
      if (loadId !== latestLoadRef.current) {
        return;
      }
      if (result.ok) {
        snapshotRef.current = result.snapshot;
        automaticFailuresRef.current = 0;
        setSnapshot(result.snapshot);
        setProblem(null);
      } else {
        if (manual || snapshotRef.current === null) {
          setProblem(result.problem);
          return;
        }
        automaticFailuresRef.current += 1;
        if (automaticFailuresRef.current >= 2) {
          setProblem(result.problem);
        }
      }
    },
    [fetcher],
  );

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const poll = async () => {
      await load(false);
      if (!cancelled) {
        timer = setTimeout(() => {
          void poll();
        }, pollMs);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) {
        clearTimeout(timer);
      }
    };
  }, [load, pollMs]);

  const run = useCallback(
    async (action: ControlAction) => {
      setPending(true);
      setControlError(null);
      const result = await applyControl(action, fetcher);
      setPending(false);
      if (result.snapshot !== null) {
        snapshotRef.current = result.snapshot;
        setSnapshot(result.snapshot);
      }
      if (result.status !== "applied") {
        setControlError(result.message ?? COPY.notApplied);
      }
    },
    [fetcher],
  );

  const openConfirm = useCallback((request: ConfirmRequest) => {
    setControlError(null);
    setConfirmRequest(request);
  }, []);

  const confirm = useCallback(
    async (reason: string) => {
      if (snapshot === null || confirmRequest === null) {
        return;
      }
      const request = confirmRequest;
      setConfirmRequest(null);
      if (request.kind === "unlock") {
        await run(unlockNewMoneyOrders(snapshot, reason));
        return;
      }
      if (request.kind === "stop-all") {
        await run(
          stopBrokerAccess(snapshot, null, "Operator stopped all brokered REST access."),
        );
        return;
      }
      const app = snapshot.apps.find((entry) => entry.appId === request.appId);
      await run(
        stopBrokerAccess(
          snapshot,
          request.appId,
          `Operator stopped brokered REST access for ${app?.name ?? request.appId}.`,
        ),
      );
    },
    [confirmRequest, run, snapshot],
  );

  return (
    <div className="shell">
      {snapshot === null ? (
        <h1 className="product">
          <span className="product__name">{COPY.product}</span>{" "}
          <span className="product__descriptor">— {COPY.descriptor}</span>
        </h1>
      ) : (
        <Header
          snapshot={snapshot}
          now={now}
          refreshing={refreshing}
          onRefresh={() => {
            void load(true);
          }}
        />
      )}

      {snapshot !== null ? (
        <SafetyStrip
          snapshot={snapshot}
          pending={pending}
          onStopNewMoneyOrders={() => {
            void run(stopNewMoneyOrders(snapshot));
          }}
          onUnlock={() => openConfirm(unlockRequest(snapshot))}
          onStopAllAccess={() => openConfirm(stopAllRequest(snapshot))}
          onResumeAllAccess={() => {
            void run(resumeBrokerAccess(snapshot, null));
          }}
        />
      ) : null}

      {controlError !== null ? (
        <p className="notice notice--danger" role="alert">
          {controlError}
        </p>
      ) : null}

      {problem !== null && snapshot !== null ? (
        <p className="notice notice--warning" role="status">
          {problem.message} This window is showing the last good sample.
        </p>
      ) : null}

      {snapshot !== null ? (
        <>
          <section className="budget-grid" aria-label="Rate budgets">
            {snapshot.budgets.map((budget) => (
              <BudgetCard key={budget.id} budget={budget} />
            ))}
          </section>

          <div className="columns">
            <section className="card applications" aria-label="Applications">
              <h2 className="section-heading">Applications</h2>
              <AppTable
                snapshot={snapshot}
                now={now}
                pending={pending}
                detailAppId={detailAppId}
                onToggleDetail={(appId) => {
                  setDetailAppId((current) => (current === appId ? null : appId));
                }}
                onStopApp={(appId) => {
                  const app = snapshot.apps.find((entry) => entry.appId === appId);
                  if (app !== undefined) {
                    openConfirm(appStopRequest(app));
                  }
                }}
                onResumeApp={(appId) => {
                  void run(resumeBrokerAccess(snapshot, appId));
                }}
              />
            </section>
            <ActivityFeed snapshot={snapshot} />
          </div>
        </>
      ) : problem !== null ? (
        <ProblemPanel
          problem={problem}
          onRetry={() => {
            void load(true);
          }}
        />
      ) : null}

      {confirmRequest !== null ? (
        <ConfirmDialog
          request={confirmRequest}
          pending={pending}
          error={null}
          onCancel={() => setConfirmRequest(null)}
          onConfirm={(reason) => {
            void confirm(reason);
          }}
        />
      ) : null}
    </div>
  );
}

function useNow(clock: () => number): number {
  const clockRef = useRef(clock);
  clockRef.current = clock;
  const [now, setNow] = useState<number>(() => clock());
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(clockRef.current());
    }, 1_000);
    return () => {
      clearInterval(timer);
    };
  }, []);
  return now;
}

function appStopRequest(app: AppRow): ConfirmRequest {
  return {
    kind: "app-stop",
    appId: app.appId,
    title: "Stop brokered REST access",
    body: "Brokered REST reads stop for these caller labels. Direct venue requests and WebSocket streams are not affected, and the application keeps running.",
    affected: app.callers.length > 0 ? app.callers : [app.name],
    phrase: null,
    reasonRequired: false,
    confirmLabel: "Stop access",
    destructive: true,
  };
}

function stopAllRequest(snapshot: GovernorSnapshot): ConfirmRequest {
  return {
    kind: "stop-all",
    appId: null,
    title: "Stop all brokered REST access",
    body: "Every brokered REST read on this machine stops, for known and unknown callers. Direct venue requests and WebSocket streams are not affected.",
    affected: [...snapshot.apps.map((app) => app.name), "Unknown callers"],
    phrase: "STOP ALL",
    reasonRequired: false,
    confirmLabel: "Stop all brokered REST access",
    destructive: true,
  };
}

function unlockRequest(snapshot: GovernorSnapshot): ConfirmRequest {
  const lock = snapshot.tradeLock;
  return {
    kind: "unlock",
    appId: lock.appId,
    title: `Unlock ${lock.appName} new money orders`,
    body: `This allows ${lock.appName} to submit new real-money orders again. Existing orders were not cancelled, and nothing is cancelled or flattened here.`,
    affected: [],
    phrase: lock.confirmationPhrase,
    reasonRequired: true,
    confirmLabel: "Unlock",
    destructive: false,
  };
}

function ProblemPanel({
  problem,
  onRetry,
}: {
  readonly problem: SnapshotProblem;
  readonly onRetry: () => void;
}) {
  return (
    <section className="card panel" role="alert">
      <h2 className="section-heading">{problem.message}</h2>
      <p className="detail__note">
        {problem.kind === "unsupported-schema"
          ? "Update Governor so the window and the service speak the same version."
          : "Nothing was changed. Start Governor, then refresh this window."}
      </p>
      <button type="button" className="btn btn--quiet" onClick={onRetry}>
        <RefreshIcon />
        Refresh
      </button>
    </section>
  );
}
