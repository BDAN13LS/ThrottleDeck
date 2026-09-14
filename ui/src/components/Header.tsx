import type { GovernorSnapshot } from "../contracts";
import { COPY, brokerHealthLabel, brokerHealthTone, formatAge } from "../derive";
import { RefreshIcon } from "./icons";

export interface HeaderProps {
  readonly snapshot: GovernorSnapshot;
  readonly now: number;
  readonly refreshing: boolean;
  readonly onRefresh: () => void;
}

export function Header({ snapshot, now, refreshing, onRefresh }: HeaderProps) {
  const tone = brokerHealthTone(snapshot, now);
  return (
    <header className="header">
      <h1 className="product">
        <span className="product__name">{COPY.product}</span>{" "}
        <span className="product__descriptor">— {COPY.descriptor}</span>
      </h1>
      <div className="header__state">
        <span className="status" data-tone={tone}>
          <span className="dot" data-shape="status" aria-hidden="true" />
          {brokerHealthLabel(snapshot, now)}
        </span>
        <span className="header__divider" aria-hidden="true" />
        <span className="header__age">
          {formatAge(snapshot.broker.sampledAt, now)}
        </span>
        <button
          type="button"
          className="btn btn--quiet"
          onClick={onRefresh}
          disabled={refreshing}
        >
          <RefreshIcon />
          Refresh
        </button>
      </div>
      {snapshot.broker.collectionPaused ? (
        <p className="notice notice--warning" role="status">
          History collection is paused.{" "}
          {snapshot.broker.pauseReason ?? "No reason was recorded."} Safety
          controls remain available.
        </p>
      ) : null}
    </header>
  );
}
