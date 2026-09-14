# ThrottleDeck visual implementation contract

Approved product design: the compact iOS-dark ThrottleDeck information architecture.

Visual references:

- `governor-ios-dark-concept.png` — primary dark visual reference. Its palette,
  density, borders, and control
  treatment are approved; its invented footer, gear icon, metrics, and activity
  copy are not product requirements.
- `governor-dark-940x700.png` — verified live implementation at the exact startup
  viewport.
- `governor-compact-approved.png` — compact 720 by 480 composition and stopped state. The source bitmap is rendered at double density; its layout target is 720 by 480 CSS pixels.

The reference images are implementation specifications. Their visible metrics are representative fixtures, not claims about live state. Runtime text and controls remain code-native.

## Color lock

The canvas is cool near-black, never brown or warm charcoal: `#07090D`.
Surfaces are graphite `#11141A`; raised surfaces are `#171B23`.

- Primary text: `#F5F7FA`
- Secondary text: `#B4BAC5`
- Muted text: `#7D8592`
- Border: `#252B35`
- Strong border: `#39414E`
- Accent: `#0A84FF`
- Accent hover: `#409CFF`
- Healthy: `#30D158`
- Warning: `#FF9F0A`
- Danger: `#FF453A`
- Intentional stop: `#A9B0BC`

No brown tint, gradients, neon glows, decorative glass, or crypto colors are permitted.

## Typography

Font stack: `"Segoe UI Variable", "Segoe UI", system-ui, sans-serif`.

- Product name: 25 px default / 21 px compact, weight 700, line-height 1.1.
- Product descriptor: same line, weight 400.
- Section headings: 18 px, weight 700, line-height 1.2.
- Card heading: 16 px, weight 700.
- Primary metric: 26 px, weight 700, tabular numerals.
- Row name: 14 px, weight 650.
- Body and controls: 13 px, weight 400–600, line-height 1.4.
- Labels/captions: 11 px, weight 500, line-height 1.35.

Every changing measurement uses `font-variant-numeric: tabular-nums`. Buttons and table cells declare their typography explicitly rather than inheriting browser defaults.

## Geometry and containers

- Edge app opens at 940 by 700 pixels and remains freely resizable.
- Page padding: 18 px default, 12 px compact.
- Grid gap: 12 px default, 10 px compact.
- Four-pixel spacing scale: 4, 8, 12, 16, 24, 32, 48.
- Cards: 15 px radius, 1 px cool-gray border, restrained cool shadow.
- Buttons: 10 px radius, minimum 36 px target height.
- Safety strip: one full-width bordered band, never nested cards.
- Applications: a table/open list, never a card grid.
- Activity: one adjacent panel on desktop; one collapsed warning row on compact.
- The 940 by 700 Edge frame has roughly 920 by 650 usable CSS pixels. That
  startup content area must not create a horizontal or vertical page scrollbar.

## Allowed first-viewport copy

No additional eyebrow, promotional text, navigation, badges, or labels may be invented above the fold.

- `ThrottleDeck`
- `API Control`
- `ThrottleDeck Broker healthy`
- `Healthy`
- `Updated now`
- `Refresh`
- `<configured app> new money orders`
- `Allowed`
- `Stopped`
- `<configured app> may submit new real-money orders.`
- `Existing orders were not cancelled.`
- `Stop new money orders`
- `Unlock`
- `Stop all brokered REST access`
- `Polymarket REST`
- `Kalshi Predictions`
- `Kalshi Perps`
- `Available now`
- `Refill rate`
- `Queue depth`
- `Recent pressure`
- `Applications`
- `Name`
- `Brokered REST`
- `Recent requests`
- `Brokered REST activity · WebSockets not measured`
- `REST traffic unavailable`
- `No REST recorded`
- `Last REST now`
- `Queue wait`
- `Errors`
- `Access`
- `Signal Collector`
- `Execution Bot`
- `Market Research`
- `Paper`
- `Real money`
- `Yes`
- `Stop access`
- `Control activity`
- `Possible direct caller`
- `Polymarket throttled this machine while ThrottleDeck Broker still had room.`
- `Broker started`
- `Access policy`
- `Application started`

Dynamic health, age, measurement, and event values may replace their fixture equivalents without changing the label hierarchy.

## Icon inventory

- Refresh: 16 px clockwise arrow, 1.75 px outline, accent color.
- Health/status: 10 px filled circle plus adjacent text. Color is never the only state cue.
- Money safety: 28 px shield or stop-square, filled semantic color, no decorative container.
- Warning: 20 px outlined triangle with exclamation, warning color.
- Disclosure: 16 px downward chevron, 1.75 px outline.

Use one consistent SVG icon family with round caps and joins. No emoji, text glyph arrows, or mixed filled/outline metaphors.

## Components and states

- `Header`: product identity, broker health, update age, refresh.
- `SafetyStrip`: allowed, stopping, stopped, unavailable; separate money stop from global REST stop.
- `BudgetCard`: healthy, pressure, queued, unavailable, perps-unconfigured.
- `ApplicationTable`: allowed/stopped/unassigned rows, brokered-REST request
  deltas, last-request age, and expandable details. Null series points are chart
  gaps, never zeroes.
- `ActivityFeed`: ordinary control/policy events and the high-priority
  possible-direct-caller warning.
- `ConfirmDialog`: per-app stop, `STOP ALL`, and the configured unlock phrase.
- `Sparkline`: SVG only, with an adjacent accessible text summary.

Controls never update optimistically. A stop button retains its prior state until the server confirms the new revision. Focus rings are 2 px accent with a 2 px offset. Reduced-motion disables value interpolation and panel movement.

## Responsive contract

At the 940 by 700 startup size, all three budgets remain in one row, application
rows use their concise layout, and Control activity starts as one expandable priority
row. The approximately 920 by 650 usable area inside the Windows frame must fit
without horizontal or vertical page scrolling. At
720 by 480 CSS pixels:

- Product header compresses to one line plus update state.
- The safety strip remains first and fully operable.
- Budget cards remain three compact summaries when readable; below that width they stack.
- Application rows use a compact two-line layout without horizontal scrolling.
- Activity reduces to the latest warning/event disclosure row.
- The money-lock state and recovery action may never be hidden in an overflow menu.

## Prohibited deviations

- No sidebar, marketing hero, search, tabs, or decorative dashboard navigation.
- No monthly quota or reset clock without an authoritative source.
- No claim that orders pass through ThrottleDeck Broker.
- No claim that a paper application uses real money.
- No process name, IP address, or direct-caller identity unless a later verified feature supplies it.
- No order, cancel, flatten, or restart-broker control.
- No static screenshot shipped as UI.
