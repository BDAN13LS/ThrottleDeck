/** One consistent outline icon family: round caps, round joins, no emoji. */

interface IconProps {
  readonly size?: number;
}

export function RefreshIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M13.5 8a5.5 5.5 0 1 1-1.9-4.16" />
      <path d="M13.6 1.9v2.9h-2.9" />
    </svg>
  );
}

export function ShieldIcon({ size = 28 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      aria-hidden="true"
      focusable="false"
      fill="currentColor"
    >
      <path d="M14 1.8 24 5.2v8.1c0 6.2-4.1 10.6-10 12.9C8.1 23.9 4 19.5 4 13.3V5.2Z" />
      <circle cx="14" cy="13" r="3.4" fill="#FFFFFF" />
    </svg>
  );
}

export function StopSquareIcon({ size = 28 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      aria-hidden="true"
      focusable="false"
      fill="currentColor"
    >
      <rect x="2.5" y="2.5" width="23" height="23" rx="6.5" />
      <rect x="9" y="9" width="10" height="10" rx="2" fill="#FFFFFF" />
    </svg>
  );
}

export function WarningTriangleIcon({ size = 20 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10 2.6 18 17H2Z" />
      <path d="M10 7.6v4.1" />
      <path d="M10 14.2h.01" />
    </svg>
  );
}

export function ChevronDownIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 6.2 8 10.1l4-3.9" />
    </svg>
  );
}
