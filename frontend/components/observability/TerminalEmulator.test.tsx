import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TerminalEmulator } from "./TerminalEmulator";

/**
 * #1245: the pod shell grew downward without bound instead of scrolling
 * inside itself. The host div was `min-h-[20rem]` with no bounded height, so
 * fit() sized `rows` to the host, xterm rendered them, the auto-height host
 * grew, the ResizeObserver re-fit, and the terminal walked down the page.
 *
 * jsdom has no layout engine, so "does it grow?" can't be measured directly —
 * every element is 0x0. What *is* checkable, and what actually regressed, is
 * the sizing contract: the host must fill a definite-height ancestor rather
 * than size itself to its content. These tests pin that contract.
 */

const term = vi.hoisted(() => ({
  open: vi.fn(),
  dispose: vi.fn(),
  loadAddon: vi.fn(),
  onData: vi.fn(() => ({ dispose: vi.fn() })),
  write: vi.fn(),
  writeln: vi.fn(),
  rows: 24,
  cols: 80,
}));

// Arrow functions aren't constructible, and the component calls `new`.
vi.mock("@xterm/xterm", () => ({
  Terminal: function () {
    return term;
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: function () {
    return { fit: vi.fn() };
  },
}));
vi.mock("@xterm/addon-web-links", () => ({
  WebLinksAddon: function () {
    return {};
  },
}));
vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

// jsdom ships no ResizeObserver; the component observes its host on mount.
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    disconnect() {}
  }
);

/**
 * Render with no pod so the WebSocket effect bails out early — the sizing
 * contract lives in the JSX and is independent of the exec session.
 */
function renderTerminal(className?: string) {
  render(<TerminalEmulator appSlug="acme" podName="" container="" className={className} />);
  const host = screen.getByRole("region");
  return { host, wrapper: host.parentElement as HTMLElement };
}

describe("TerminalEmulator sizing (#1245)", () => {
  it("fills a definite-height wrapper instead of growing with its content", () => {
    const { host, wrapper } = renderTerminal();

    // The wrapper carries a real height, not just a minimum. A `min-h-*`-only
    // wrapper is what let the terminal grow without bound.
    expect(wrapper.className).toMatch(/(^|\s)h-\S+/);
    expect(wrapper.className).not.toMatch(/(^|\s)min-h-\[/);

    // The host fills that wrapper and is allowed to shrink below its content
    // (`min-h-0`), which is what lets .xterm-viewport scroll rather than the
    // page.
    expect(host.className).toContain("flex-1");
    expect(host.className).toContain("min-h-0");
    expect(host.className).not.toMatch(/(^|\s)min-h-\[/);
  });

  it("defaults to a bounded height when the caller supplies none", () => {
    // The console mounted this with no className at all, so a default that
    // only appears when a caller opts in would not have fixed the bug.
    const { wrapper } = renderTerminal();
    expect(wrapper.className).toMatch(/(^|\s)h-96(\s|$)/);
  });

  it("lets a caller override the default height", () => {
    const { wrapper } = renderTerminal("h-[40rem]");
    // twMerge keeps the caller's height and drops the default; both surviving
    // would make the applied height depend on stylesheet order.
    expect(wrapper.className).toContain("h-[40rem]");
    expect(wrapper.className).not.toMatch(/(^|\s)h-96(\s|$)/);
  });
});
