import type { Tone } from "../derive";

/** The approved design draws a trend line and a compact distribution. */
export type SparklineShape = "line" | "bars";

/**
 * `accent` is the capacity hue from the approved desktop card: the accent
 * stroke with a soft area wash below it. `tone` keeps a series coloured by its
 * health, which is how the small pressure chart is drawn.
 */
export type SparklineHue = "tone" | "accent";

export interface SparklineProps {
  readonly values: readonly (number | null)[];
  /** Health colour, used when `hue` is `tone`. */
  readonly tone?: Tone;
  readonly hue?: SparklineHue;
  readonly shape?: SparklineShape;
  /** The accessible text summary that stands in for the graphic. */
  readonly summary: string;
  readonly width?: number;
  readonly height?: number;
  /** A repeat of a summary already announced elsewhere on the screen. */
  readonly decorative?: boolean;
}

/**
 * A small SVG trend line. The graphic is never the only source of information:
 * its accessible name always carries the text summary.
 */
export function Sparkline({
  values,
  tone = "neutral",
  hue = "tone",
  shape = "line",
  summary,
  width = 120,
  height = 36,
  decorative = false,
}: SparklineProps) {
  if (values.length < 2) {
    return null;
  }
  // The graphic is never the only source of information.
  const accessibility = decorative
    ? { "aria-hidden": true as const }
    : { role: "img" as const, "aria-label": summary };

  return (
    <span className="sparkline" data-tone={tone} data-hue={hue} data-shape={shape}>
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        {...accessibility}
        focusable="false"
      >
        {shape === "bars" ? (
          distributionBars(values, width, height)
        ) : (
          trendLine(values, width, height, hue === "accent")
        )}
      </svg>
    </span>
  );
}

/** One polyline through the samples, plus an optional area wash. */
function trendLine(
  values: readonly (number | null)[],
  width: number,
  height: number,
  area: boolean,
) {
  // Nullable values are used by request bars to preserve an unknown interval.
  // Budget trends remain fully numeric; filtering here keeps that path simple.
  const numeric = values.filter((value): value is number => value !== null);
  if (numeric.length < 2) {
    return null;
  }
  const min = Math.min(...numeric);
  const max = Math.max(...numeric);
  const span = max - min || 1;
  const stepX = width / (numeric.length - 1);
  const points = numeric
    .map((value, index) => {
      const x = index * stepX;
      const y = height - 1 - ((value - min) / span) * (height - 2);
      return `${round(x)},${round(y)}`;
    })
    .join(" ");

  return (
    <>
      {area ? (
        <polygon
          points={`${points} ${round(width)},${height} 0,${height}`}
          fill="currentColor"
          fillOpacity={0.12}
          stroke="none"
        />
      ) : null}
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>
  );
}

/** One bar per sample, scaled against the largest sample in the window. */
function distributionBars(
  values: readonly (number | null)[],
  width: number,
  height: number,
) {
  const max =
    Math.max(...values.filter((value): value is number => value !== null)) || 1;
  const slot = width / values.length;
  const barWidth = Math.max(1, slot - Math.min(2, slot * 0.3));
  return values.map((value, index) => {
    if (value === null) {
      return null;
    }
    const barHeight = Math.max(1, (value / max) * (height - 2));
    return (
      <rect
        key={index}
        x={round(index * slot + (slot - barWidth) / 2)}
        y={round(height - barHeight)}
        width={round(barWidth)}
        height={round(barHeight)}
        fill="currentColor"
        fillOpacity={0.55}
      />
    );
  });
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}
