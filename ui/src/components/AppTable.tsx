import { Fragment } from "react";

import type { AppRow, GovernorSnapshot } from "../contracts";
import {
  appAccessLabel,
  appMoneyLabel,
  describeRequestSeries,
  formatBrokeredRest,
  formatErrors,
  formatLastRest,
  formatQueueWait,
  formatRecentRequests,
} from "../derive";
import { useCompact } from "../useCompact";
import { AppDetail } from "./AppDetail";
import { ChevronDownIcon } from "./icons";
import { Sparkline } from "./Sparkline";

export interface AppTableProps {
  readonly snapshot: GovernorSnapshot;
  readonly now: number;
  readonly pending: boolean;
  readonly detailAppId: string | null;
  readonly onToggleDetail: (appId: string) => void;
  readonly onStopApp: (appId: string) => void;
  readonly onResumeApp: (appId: string) => void;
}

export function AppTable({
  snapshot,
  now,
  pending,
  detailAppId,
  onToggleDetail,
  onStopApp,
  onResumeApp,
}: AppTableProps) {
  const compact = useCompact(1_000);

  return (
    <table className="table">
      <caption className="table__caption">
        Brokered REST activity · WebSockets not measured
      </caption>
      <thead>
        <tr>
          <th scope="col">Name</th>
          {compact ? null : <th scope="col">Brokered REST</th>}
          {compact ? null : <th scope="col">Recent requests</th>}
          {compact ? null : <th scope="col">Queue wait</th>}
          {compact ? null : <th scope="col">Errors</th>}
          <th scope="col">Access</th>
        </tr>
      </thead>
      <tbody>
        {snapshot.apps.map((app) => (
          <Fragment key={app.appId}>
            <tr className="table__row">
              <th scope="row" className="table__name">
                <button
                  type="button"
                  className="row-toggle"
                  aria-expanded={detailAppId === app.appId}
                  aria-controls={`app-detail-${app.appId}`}
                  aria-label={`${app.name} details`}
                  onClick={() => onToggleDetail(app.appId)}
                >
                  <span className="row-name">{app.name}</span>
                  <ChevronDownIcon />
                </button>
                <span className="row-mode">{appMoneyLabel(app)}</span>
                {compact ? (
                  <span className="row-summary">
                    <Sparkline
                      values={app.requestSeries}
                      hue="accent"
                      shape="bars"
                      width={48}
                      height={14}
                      summary={describeRequestSeries(app)}
                    />
                    <span>{formatRecentRequests(app)}</span>
                    <span aria-hidden="true">·</span>
                    <span>{formatLastRest(app, now)}</span>
                    <span aria-hidden="true">·</span>
                    <span>{formatQueueWait(app)}</span>
                    <span aria-hidden="true">·</span>
                    <span>{formatErrors(app)} errors</span>
                  </span>
                ) : null}
              </th>
              {compact ? null : (
                <td>
                  <span className="status" data-tone={accessTone(app)}>
                    <span className="dot" data-shape="status" aria-hidden="true" />
                    {formatBrokeredRest(app)}
                  </span>
                </td>
              )}
              {compact ? null : <td className="table__num">{formatRecentRequests(app)}</td>}
              {compact ? null : <td className="table__num">{formatQueueWait(app)}</td>}
              {compact ? null : <td className="table__num">{formatErrors(app)}</td>}
              <td className="table__access">
                <span className="sr-only">{appAccessLabel(app)}</span>
                {app.access === "stopped" ? (
                  <button
                    type="button"
                    className="btn btn--quiet"
                    onClick={() => onResumeApp(app.appId)}
                    disabled={pending}
                  >
                    Resume access
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn--stop"
                    onClick={() => onStopApp(app.appId)}
                    disabled={pending}
                  >
                    Stop access
                  </button>
                )}
              </td>
            </tr>
            {detailAppId === app.appId ? (
              <tr className="table__detail-row">
                <td colSpan={compact ? 2 : 6}>
                  <div id={`app-detail-${app.appId}`}>
                    <AppDetail snapshot={snapshot} app={app} now={now} />
                  </div>
                </td>
              </tr>
            ) : null}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

function accessTone(app: AppRow): "healthy" | "stop" {
  return app.access === "stopped" ? "stop" : "healthy";
}
