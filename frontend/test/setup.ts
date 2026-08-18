import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom does not implement the browser observer used by the shared graph
// canvas. Keep the polyfill global so every DAG/topology surface exercises
// the real component instead of each test inventing a one-off mock.
Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
});

// jsdom implements neither the scroll helper nor the pointer-capture API that
// Radix's popover primitives (Select, DropdownMenu) call on mount. Without
// them, opening any Select in a test throws and the surface can only be
// covered by mocking the component away — which tests the mock. Same reasoning
// as the ResizeObserver polyfill above: fix it once, globally.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = function hasPointerCapture() {
    return false;
  };
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = function releasePointerCapture() {};
}

// Node 25 exposes an experimental localStorage object that is unusable when
// Vitest workers do not receive a --localstorage-file path. Pin tests to the
// browser Storage contract so local and CI runners behave identically.
const storageValues = new Map<string, string>();
const memoryStorage: Storage = {
  get length() {
    return storageValues.size;
  },
  clear() {
    storageValues.clear();
  },
  getItem(key) {
    return storageValues.get(key) ?? null;
  },
  key(index) {
    return Array.from(storageValues.keys())[index] ?? null;
  },
  removeItem(key) {
    storageValues.delete(key);
  },
  setItem(key, value) {
    storageValues.set(key, String(value));
  },
};
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: memoryStorage,
});
Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: memoryStorage,
});

// We run with ``globals: false``, so React Testing Library's built-in
// afterEach auto-cleanup (which only registers when a global ``afterEach``
// exists) doesn't fire. Unmount + reset the DOM between tests ourselves so
// rendered trees don't leak across cases.
afterEach(() => {
  cleanup();
});
