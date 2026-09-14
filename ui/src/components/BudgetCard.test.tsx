import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BudgetCard } from "./BudgetCard";
import type { Budget } from "../contracts";
import { restoreViewportWidth, setViewportWidth, snapshotOf } from "../test/support";

function budgetOf(name: Parameters<typeof snapshotOf>[0], id: Budget["id"]): Budget {
  const budget = snapshotOf(name).budgets.find((entry) => entry.id === id);
  if (budget === undefined) {
    throw new Error(`fixture has no budget ${id}`);
  }
  return budget;
}

describe("BudgetCard", () => {
  it("labels local capacity as available now, never as a monthly quota", () => {
    render(<BudgetCard budget={budgetOf("healthy", "kalshi.predictions")} />);
    expect(screen.getByText("Kalshi Predictions")).toBeVisible();
    expect(screen.getByText("Available now")).toBeVisible();
    expect(screen.getByText("540 / 540")).toBeVisible();
    expect(screen.getByText("tokens")).toBeVisible();
    expect(screen.getByText("Refill rate")).toBeVisible();
    expect(screen.getByText("10 tokens/s")).toBeVisible();
    expect(screen.getByText("Queue depth")).toBeVisible();
    expect(screen.getByText("Recent pressure")).toBeVisible();
    expect(screen.getByText("12%")).toBeVisible();
    expect(screen.queryByText(/monthly quota/i)).toBeNull();
    expect(screen.queryByText(/per month/i)).toBeNull();
  });

  it("carries the status as text and shape, not colour alone", () => {
    const { container } = render(<BudgetCard budget={budgetOf("healthy", "pmus")} />);
    expect(screen.getByText("Healthy")).toBeVisible();
    expect(container.querySelector("[data-shape='status']")).not.toBeNull();
  });

  it("reports pressure with amber wording plus text", () => {
    const { container } = render(
      <BudgetCard budget={budgetOf("warning", "kalshi.predictions")} />,
    );
    expect(screen.getByText("At limit")).toBeVisible();
    expect(container.querySelector("[data-tone='critical']")).not.toBeNull();
  });

  it("never invents numbers for an unconfigured product", () => {
    render(<BudgetCard budget={budgetOf("unconfiguredPerps", "kalshi.perps")} />);
    expect(screen.getByText("Not configured")).toBeVisible();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 / 0")).toBeNull();
    expect(screen.queryByText("0 tokens/s")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("shows unavailable rather than zero after the broker goes down", () => {
    render(<BudgetCard budget={budgetOf("brokerDown", "pmus")} />);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getByText("Available now")).toBeVisible();
    expect(screen.queryByText("0 / 0")).toBeNull();
  });

  it("summarises the sparkline in text", () => {
    render(<BudgetCard budget={budgetOf("healthy", "pmus")} />);
    expect(screen.getByRole("img", { name: /Polymarket REST/ })).toBeVisible();
  });

  it("draws the capacity trend in the accent hue, as the approved design does", () => {
    const { container } = render(<BudgetCard budget={budgetOf("healthy", "pmus")} />);
    const capacity = container.querySelector(".budget__value .sparkline");
    expect(capacity?.getAttribute("data-hue")).toBe("accent");
  });

  it("keeps a compact micro-chart with its text summary", () => {
    setViewportWidth(720);
    try {
      const { container } = render(<BudgetCard budget={budgetOf("healthy", "pmus")} />);
      const chart = container.querySelector(".budget__value .sparkline");
      expect(chart?.getAttribute("data-shape")).toBe("bars");
      expect(screen.getByRole("img", { name: /Polymarket REST/ })).toBeVisible();
    } finally {
      restoreViewportWidth();
    }
  });
});
