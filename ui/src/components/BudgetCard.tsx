import type { Budget } from "../contracts";
import {
  budgetStatusLabel,
  budgetTitle,
  budgetTone,
  budgetUnitLabel,
  describeSeries,
  formatAvailable,
  formatBudgetPressure,
  formatQueueDepth,
  formatRefill,
  isBudgetReadable,
} from "../derive";
import { useCompact } from "../useCompact";
import { Sparkline } from "./Sparkline";

export interface BudgetCardProps {
  readonly budget: Budget;
}

export function BudgetCard({ budget }: BudgetCardProps) {
  const tone = budgetTone(budget);
  const readable = isBudgetReadable(budget);
  const summary = describeSeries(budget);
  const compact = useCompact();

  return (
    <article
      className="card budget"
      data-tone={tone}
      aria-labelledby={`budget-${budget.id}`}
    >
      <div className="budget__head">
        <h3 className="card__heading" id={`budget-${budget.id}`}>
          {budgetTitle(budget)}
        </h3>
        <span className="status" data-tone={tone}>
          <span className="dot" data-shape="status" aria-hidden="true" />
          {budgetStatusLabel(budget)}
        </span>
      </div>

      <div className="budget__value">
        <div className="budget__figure">
          {compact ? null : <span className="label">Available now</span>}
          <span className="metric">{formatAvailable(budget)}</span>
          <span className="label">{budgetUnitLabel(budget)}</span>
        </div>
        {readable ? (
          compact ? (
            <Sparkline
              values={budget.series}
              tone={tone}
              shape="bars"
              width={44}
              height={22}
              summary={summary}
            />
          ) : (
            <Sparkline
              values={budget.series}
              tone={tone}
              hue="accent"
              summary={summary}
            />
          )
        ) : null}
      </div>

      {readable ? (
        <div className="budget__capacity" aria-hidden="true">
          <span
            className="budget__capacity-fill"
            style={{ inlineSize: capacityPercent(budget) + "%" }}
          />
        </div>
      ) : null}

      {compact ? (
        <div className="budget__footer budget__footer--compact">
          <span className="label">Available now</span>
          <span className="value">
            Queue depth {formatQueueDepth(budget)}
          </span>
        </div>
      ) : (
        <div className="budget__footer">
          <div className="budget__stat">
            <span className="label">Refill rate</span>
            <span className="value">{formatRefill(budget)}</span>
          </div>
          <div className="budget__stat">
            <span className="label">Queue depth</span>
            <span className="value">{formatQueueDepth(budget)}</span>
          </div>
          <div className="budget__stat">
            <span className="label">Recent pressure</span>
            <span className="value">
              {formatBudgetPressure(budget)}
              {readable ? (
                <Sparkline
                  values={budget.series}
                  tone={tone}
                  summary={summary}
                  width={56}
                  height={18}
                  decorative
                />
              ) : null}
            </span>
          </div>
        </div>
      )}
    </article>
  );
}

function capacityPercent(budget: Budget): number {
  if (
    budget.availableNow === null ||
    budget.capacity === null ||
    budget.capacity === 0
  ) {
    return 0;
  }
  return Math.max(
    0,
    Math.min(100, Math.round((budget.availableNow / budget.capacity) * 100)),
  );
}
