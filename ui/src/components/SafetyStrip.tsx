import type { GovernorSnapshot } from "../contracts";
import { moneyLockView } from "../derive";
import { ShieldIcon, StopSquareIcon } from "./icons";

export interface SafetyStripProps {
  readonly snapshot: GovernorSnapshot;
  readonly pending: boolean;
  readonly onStopNewMoneyOrders: () => void;
  readonly onUnlock: () => void;
  readonly onStopAllAccess: () => void;
  readonly onResumeAllAccess: () => void;
}

/**
 * One full-width band with two separate decisions: the configured money stop and
 * the machine-wide broker-access stop. Neither is nested in a card, and neither
 * claims to have stopped a process, a direct request, or a WebSocket stream.
 */
export function SafetyStrip({
  snapshot,
  pending,
  onStopNewMoneyOrders,
  onUnlock,
  onStopAllAccess,
  onResumeAllAccess,
}: SafetyStripProps) {
  const view = moneyLockView(snapshot.tradeLock);
  const readsStopped = snapshot.brokerAccess.global.state === "stopped";
  const moneyGlyph = view.tone === "healthy" ? <ShieldIcon /> : <StopSquareIcon />;

  return (
    <section className="strip" data-tone={view.tone}>
      <div className="strip__grid">
        <div
          className="strip__money"
          role="group"
          aria-labelledby="money-lock-heading"
        >
          <span className="strip__glyph" data-tone={view.tone} aria-hidden="true">
            {moneyGlyph}
          </span>
          <div className="strip__text">
            <div className="strip__title-row">
              <h2 className="strip__heading" id="money-lock-heading">
                {view.heading}
              </h2>
              <span className="strip__rule" aria-hidden="true" />
              <span className="status" data-tone={view.tone}>
                <span className="dot" data-shape="status" aria-hidden="true" />
                {view.stateLabel}
              </span>
            </div>
            <p className="strip__detail">{view.detail}</p>
          </div>
        </div>

        {readsStopped ? (
          <div className="strip__reads" role="group" aria-label="Brokered REST reads">
            <div className="strip__text">
              <span className="status" data-tone="stop">
                <span className="dot" data-shape="status" aria-hidden="true" />
                Stopped
              </span>
              <p className="strip__detail">
                Every brokered REST read on this machine stops, for known and
                unknown callers. Direct venue requests and WebSocket streams are
                not affected.
              </p>
            </div>
          </div>
        ) : null}

        <div className="strip__actions">
          {view.canUnlock ? (
            <button
              type="button"
              className="btn btn--accent"
              onClick={onUnlock}
              disabled={pending}
            >
              Unlock
            </button>
          ) : null}
          {view.canStop ? (
            <button
              type="button"
              className="btn btn--danger"
              onClick={onStopNewMoneyOrders}
              disabled={pending}
            >
              Stop new money orders
            </button>
          ) : null}
          {readsStopped ? (
            <button
              type="button"
              className="btn btn--stop"
              onClick={onResumeAllAccess}
              disabled={pending}
            >
              Resume all brokered REST access
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--stop"
              onClick={onStopAllAccess}
              disabled={pending}
            >
              Stop all brokered REST access
            </button>
          )}
        </div>
      </div>

      {view.banner ? (
        <p className="strip__banner" role="status" data-tone={view.tone}>
          {view.banner}
        </p>
      ) : null}
    </section>
  );
}
