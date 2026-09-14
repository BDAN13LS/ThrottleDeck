import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Sparkline } from "./Sparkline";

describe("Sparkline", () => {
  it("exposes a text summary instead of colour alone", () => {
    render(
      <Sparkline
        values={[1, 2, 3]}
        tone="healthy"
        summary="Polymarket REST: 1 to 3 requests available."
      />,
    );
    expect(
      screen.getByRole("img", {
        name: "Polymarket REST: 1 to 3 requests available.",
      }),
    ).toBeVisible();
  });

  it("draws one point per sample and never emits NaN", () => {
    const { container } = render(
      <Sparkline values={[4, 8, 6, 9]} tone="healthy" summary="trend" />,
    );
    const points = container.querySelector("polyline")?.getAttribute("points");
    expect(points?.trim().split(/\s+/)).toHaveLength(4);
    expect(points).not.toMatch(/NaN/);
  });

  it("renders nothing when there is no series to draw", () => {
    const { container } = render(
      <Sparkline values={[]} tone="neutral" summary="no samples" />,
    );
    expect(container.querySelector("svg")).toBeNull();
  });

  it("draws a capacity series in the accent hue with a soft area wash", () => {
    const { container } = render(
      <Sparkline values={[4, 8, 6, 9]} hue="accent" summary="capacity trend" />,
    );
    const sparkline = container.querySelector(".sparkline");
    expect(sparkline?.getAttribute("data-hue")).toBe("accent");
    expect(container.querySelector("polyline")).not.toBeNull();
    expect(container.querySelector("polygon")).not.toBeNull();
  });

  it("draws a distribution as one bar per sample and keeps the summary", () => {
    const { container } = render(
      <Sparkline
        values={[4, 8, 6, 9]}
        shape="bars"
        tone="healthy"
        summary="distribution trend"
      />,
    );
    expect(container.querySelectorAll("rect")).toHaveLength(4);
    expect(container.querySelector("polyline")).toBeNull();
    expect(screen.getByRole("img", { name: "distribution trend" })).toBeVisible();
  });

  it("keeps every distribution bar inside the box and free of NaN", () => {
    const { container } = render(
      <Sparkline values={[2, 2, 2, 2]} shape="bars" height={20} summary="flat" />,
    );
    for (const rect of container.querySelectorAll("rect")) {
      for (const attribute of ["x", "y", "width", "height"]) {
        const value = Number(rect.getAttribute(attribute));
        expect(Number.isFinite(value)).toBe(true);
        expect(value).toBeGreaterThanOrEqual(0);
      }
    }
  });
});
