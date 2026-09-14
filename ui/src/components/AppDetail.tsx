import type { AppRow, GovernorSnapshot } from "../contracts";
import {
  appDetailView,
  appMoneyLabel,
  eventTitle,
  formatClock,
  formatErrors,
  formatQueueWait,
  formatRecentRequests,
  formatBrokeredRest,
} from "../derive";

export interface AppDetailProps {
  readonly snapshot: GovernorSnapshot;
  readonly app: AppRow;
  readonly now: number;
}

/**
 * The detail view states the caller labels a stop covers and the exact boundary
 * of what it does and does not affect.
 */
export function AppDetail({ snapshot, app, now }: AppDetailProps) {
  const view = appDetailView(app, snapshot.tradeLock);
  const audit = snapshot.activity.filter((event) => event.appId === app.appId);

  return (
    <section className="detail" aria-label={`${app.name} detail`}>
      <div className="detail__grid">
        <div className="detail__block">
          <h4 className="detail__heading">Callers</h4>
          {app.callers.length > 0 ? (
            <ul className="callers">
              {app.callers.map((caller) => (
                <li key={caller}>
                  <code>{caller}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="detail__note">
              No caller labels are mapped to this row.
            </p>
          )}
          {view.coverage !== null ? (
            <p className="detail__note">{view.coverage}</p>
          ) : null}
        </div>

        <div className="detail__block">
          <h4 className="detail__heading">Measurements</h4>
          <dl className="detail__stats">
            <dt>Brokered REST</dt>
            <dd>{formatBrokeredRest(app)}</dd>
            <dt>Recent requests</dt>
            <dd>{formatRecentRequests(app)}</dd>
            <dt>Queue wait</dt>
            <dd>{formatQueueWait(app)}</dd>
            <dt>Errors</dt>
            <dd>{formatErrors(app)}</dd>
          </dl>
          <h4 className="detail__heading">Venue split</h4>
          {app.venueSplit.length > 0 ? (
            <ul className="venue-split">
              {app.venueSplit.map((split) => (
                <li key={split.venue}>
                  <span>{split.venue}</span>
                  <span className="value">{split.count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="detail__note">No venue traffic was recorded.</p>
          )}
        </div>

        <div className="detail__block">
          <h4 className="detail__heading">Scope of a stop</h4>
          <p className="detail__note">{view.moneyLine}</p>
          <p className="detail__note">{view.brokeredRestLine}</p>
          <p className="detail__does">{view.doesLine}</p>
          <p className="detail__doesnot">{view.doesNotLine}</p>
          <p className="detail__note">
            Money mode: {appMoneyLabel(app)}. Last update {formatClock(new Date(now).toISOString())}.
          </p>
        </div>
      </div>

      <h4 className="detail__heading">Audit entries</h4>
      {audit.length > 0 ? (
        <ul className="audit">
          {audit.map((event) => (
            <li key={`${event.at}-${event.kind}`}>
              <span className="audit__time">{formatClock(event.at)}</span>
              <span className="audit__title">{eventTitle(event)}</span>
              {event.detail !== null ? (
                <span className="audit__detail">{event.detail}</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="detail__note">
          No recorded activity for this application yet.
        </p>
      )}
    </section>
  );
}
