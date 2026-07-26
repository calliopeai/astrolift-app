import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// We run with ``globals: false``, so React Testing Library's built-in
// afterEach auto-cleanup (which only registers when a global ``afterEach``
// exists) doesn't fire. Unmount + reset the DOM between tests ourselves so
// rendered trees don't leak across cases.
afterEach(() => {
  cleanup();
});
