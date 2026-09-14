import { useState } from "react";

import type { ActivityEvent, GovernorSnapshot } from "../contracts";
import {
  COPY,
  eventTitle,
  formatClock,
  hasHeadroomDeltaAlert,
  isHighPriorityEvent,
  latestHighPriorityEvent,
  possibleDirectCallerEvent,
  severityTone,
} from "../derive";
import { useCompact } from "../useCompact";
import { ChevronDownIcon, WarningTriangleIcon } from "./icons";

export interface ActivityFeedProps {
  readonly snapshot: GovernorSnapshot;
}

export function ActivityFeed({ snapshot }: ActivityFeedProps) {
  const compact = useCompact(1_000);
  const [expanded, setExpanded] = useState(false);
  const events = activityWithHeadroomAlert(snapshot);

  if (events.length === 0) {
    return (
      <section className="card activity" aria-label="Control activity">
        <h2 className="section-heading">Control activity</h2>
        <p className="detail__note">No recorded activity yet.</p>
      </section>
    );
  }

  if (compact) {
    const lead = latestHighPriorityEvent(snapshot) ?? (events[0] as ActivityEvent);
    const rest = events.filter((event) => event !== lead);
    return (
      <section className="card activity" aria-label="Control activity">
        <h2 className="section-heading">Control activity</h2>
        <div className="activity__lead" data-tone={severityTone(lead.severity)}>
          <button
            type="button"
            className="activity__toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            <SeverityGlyph event={lead} />
            <span className="activity__title">
              {eventTitle(lead)}
              {isHighPriorityEvent(lead) ? (
                <span className="activity__severity">
                  {severityLabel(lead.severity)}
                </span>
              ) : null}
            </span>
            <ChevronDownIcon />
          </button>
          {lead.detail !== null ? (
            <p className="activity__detail">{lead.detail}</p>
          ) : null}
        </div>
        {expanded ? (
          <ul className="activity__list">
            {rest.map((event) => (
              <li key={`${event.at}-${event.kind}`} className="activity__item">
                <span className="activity__time">{formatClock(event.at)}</span>
                <SeverityGlyph event={event} />
                <span className="activity__title">{eventTitle(event)}</span>
                {event.detail !== null ? (
                  <span className="activity__detail">{event.detail}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    );
  }

  return (
    <section className="card activity" aria-label="Control activity">
      <h2 className="section-heading">Control activity</h2>
      <ul className="activity__list">
        {events.map((event) => (
          <li
            key={`${event.at}-${event.kind}`}
            className="activity__item"
            data-tone={severityTone(event.severity)}
          >
            <span className="activity__time">{formatClock(event.at)}</span>
            <SeverityGlyph event={event} />
            <span className="activity__title">
              {eventTitle(event)}
              {isHighPriorityEvent(event) ? (
                <span className="activity__severity">
                  {severityLabel(event.severity)}
                </span>
              ) : null}
            </span>
            {event.detail !== null ? (
              <span className="activity__detail">{event.detail}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SeverityGlyph({ event }: { readonly event: ActivityEvent }) {
  if (event.severity === "info") {
    return (
      <span
        className="dot"
        data-shape="severity"
        data-tone="healthy"
        aria-hidden="true"
      />
    );
  }
  return (
    <span
      className="activity__glyph"
      data-shape="severity"
      data-tone={severityTone(event.severity)}
    >
      <WarningTriangleIcon />
    </span>
  );
}

function severityLabel(severity: ActivityEvent["severity"]): string {
  return severity === "critical" ? "Critical" : "Warning";
}

/**
 * A genuine venue throttle observed while the broker still had headroom means
 * something did not come through the broker. When the service has not logged
 * the event yet, the alert is derived from the counter delta so it is still
 * surfaced instead of silently discarded.
 */
function activityWithHeadroomAlert(
  snapshot: GovernorSnapshot,
): readonly ActivityEvent[] {
  if (!hasHeadroomDeltaAlert(snapshot) || possibleDirectCallerEvent(snapshot) !== null) {
    return snapshot.activity;
  }
  const at =
    snapshot.budgets.map((budget) => budget.sampledAt).find((value) => value !== null) ??
    snapshot.generatedAt;
  return [
    {
      at,
      kind: "possible-direct-caller",
      severity: "warning",
      detail: COPY.directCallerDetail,
      appId: null,
    },
    ...snapshot.activity,
  ];
}
