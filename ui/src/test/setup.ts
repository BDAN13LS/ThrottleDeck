import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without globals here, so Testing Library cannot auto-register its
// cleanup. Without this, renders accumulate across tests in the same file.
afterEach(() => {
  cleanup();
});
