/**
 * Minimal local typings for `jest-axe`, which ships without its own types.
 * Only the surface the Governor suite uses is declared.
 */

declare module "jest-axe" {
  export interface AxeViolation {
    readonly id: string;
    readonly impact: string | null;
    readonly description: string;
    readonly help: string;
    readonly nodes: readonly unknown[];
  }

  export interface AxeResults {
    readonly violations: readonly AxeViolation[];
    readonly passes: readonly unknown[];
    readonly incomplete: readonly unknown[];
    readonly inapplicable: readonly unknown[];
  }

  export function axe(
    html: Element | string,
    options?: Record<string, unknown>,
  ): Promise<AxeResults>;

  export function configureAxe(
    options?: Record<string, unknown>,
  ): (html: Element | string, runOptions?: Record<string, unknown>) => Promise<AxeResults>;
}
