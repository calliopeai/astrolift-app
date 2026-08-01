import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TerminalEmulator } from "./TerminalEmulator";

/**
 * #1245: the pod shell grew downward without bound instead of scrolling
 * inside itself. The host div was `min-h-[20rem]` with no bounded height, so
 * fit() sized `rows` to the host, xterm rendered them, the auto-height host
 * grew, the ResizeObserver re-fit, and the terminal walked down the page.
 *
 * #1246 then layered resize / expand / pop-out on top. The load-bearing claim
 * there is that expanding does NOT restart the exec session — it's a class
 * swap on the element the terminal already lives in, not a reparent.
 *
 * jsdom has no layout engine, so "does it grow?" can't be measured directly.
 * What *is* checkable is the sizing contract and the session lifecycle.
 */

const counts = vi.hoisted(() => ({ terminals: 0, sockets: 0 }));

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
    counts.terminals += 1;
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

// jsdom ships no WebSocket either. Count constructions — "did expanding
// restart the session?" is exactly this number changing.
vi.stubGlobal(
  "WebSocket",
  class {
    static OPEN = 1;
    readyState = 1;
    onopen: (() => void) | null = null;
    onmessage: (() => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor() {
      counts.sockets += 1;
    }
    addEventListener() {}
    send() {}
    close() {}
  }
);

beforeEach(() => {
  counts.terminals = 0;
  counts.sockets = 0;
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Render against a live target so the WebSocket effect actually runs. */
function renderLive(props: { className?: string; standalone?: boolean } = {}) {
  render(
    <TerminalEmulator
      appSlug="acme"
      podName="web-abc123"
      container="web"
      className={props.className}
      standalone={props.standalone}
    />
  );
  const host = screen.getByRole("region");
  // host -> relative positioning div -> wrapper that owns the height.
  return { host, wrapper: host.parentElement!.parentElement as HTMLElement };
}

/** Render with no pod so the WebSocket effect bails out early. */
function renderIdle(className?: string) {
  render(<TerminalEmulator appSlug="acme" podName="" container="" className={className} />);
  const host = screen.getByRole("region");
  return { host, wrapper: host.parentElement!.parentElement as HTMLElement };
}

describe("TerminalEmulator sizing (#1245)", () => {
  it("fills a definite-height wrapper instead of growing with its content", () => {
    const { host, wrapper } = renderIdle();

    // The wrapper carries a real height, not just a minimum. A `min-h-*`-only
    // wrapper is what let the terminal grow without bound.
    expect(wrapper.className).toMatch(/(^|\s)h-\S+/);
    expect(wrapper.className).not.toMatch(/(^|\s)min-h-\[/);

    // The host fills that wrapper, so .xterm-viewport scrolls rather than
    // the page.
    expect(host.className).toContain("h-full");
    expect(host.className).not.toMatch(/(^|\s)min-h-\[/);
  });

  it("defaults to a bounded height when the caller supplies none", () => {
    // The console mounted this with no className at all, so a default that
    // only appears when a caller opts in would not have fixed the bug.
    const { wrapper } = renderIdle();
    expect(wrapper.className).toMatch(/(^|\s)h-96(\s|$)/);
  });

  it("lets a caller override the default height", () => {
    const { wrapper } = renderIdle("h-[40rem]");
    // twMerge keeps the caller's height and drops the default; both surviving
    // would make the applied height depend on stylesheet order.
    expect(wrapper.className).toContain("h-[40rem]");
    expect(wrapper.className).not.toMatch(/(^|\s)h-96(\s|$)/);
  });
});

describe("TerminalEmulator expand (#1246)", () => {
  it("keeps the exec session alive across expand and collapse", () => {
    const { wrapper } = renderLive();
    expect(counts.terminals).toBe(1);
    expect(counts.sockets).toBe(1);

    fireEvent.click(screen.getByLabelText("expand"));
    expect(wrapper.className).toContain("fixed");

    fireEvent.click(screen.getByLabelText("collapse"));
    expect(wrapper.className).not.toContain("fixed");

    // The whole reason expand is a class swap rather than a portal or a
    // Dialog: reparenting or remounting would rebuild the terminal and
    // reconnect the socket, losing the scrollback and the running command.
    expect(counts.terminals).toBe(1);
    expect(counts.sockets).toBe(1);
  });

  it("drops the fixed default height while expanded so inset-0 governs", () => {
    const { wrapper } = renderLive();
    fireEvent.click(screen.getByLabelText("expand"));
    expect(wrapper.className).not.toMatch(/(^|\s)h-96(\s|$)/);
    expect(wrapper.className).toContain("inset-0");
  });

  it("collapses on Escape", () => {
    const { wrapper } = renderLive();
    fireEvent.click(screen.getByLabelText("expand"));
    expect(wrapper.className).toContain("fixed");

    act(() => {
      fireEvent.keyDown(window, { key: "Escape" });
    });
    expect(wrapper.className).not.toContain("fixed");
  });

  it("restores the dragged height when collapsing", () => {
    const { wrapper } = renderLive();
    wrapper.style.height = "500px";

    fireEvent.click(screen.getByLabelText("expand"));
    // An explicit height would fight `inset-0`, so it has to come off.
    expect(wrapper.style.height).toBe("");

    fireEvent.click(screen.getByLabelText("collapse"));
    expect(wrapper.style.height).toBe("500px");
  });
});

describe("TerminalEmulator resize (#1246)", () => {
  it("writes the dragged height and persists it per app", () => {
    const { wrapper } = renderLive();

    fireEvent.pointerDown(screen.getByLabelText("resizeHandle"), { clientY: 100 });
    fireEvent.pointerMove(window, { clientY: 400 });
    fireEvent.pointerUp(window);

    // jsdom reports a 0 starting height, so the drag lands on the delta —
    // what matters is that it tracked the pointer and survived the release.
    expect(wrapper.style.height).toBe("300px");
    expect(window.localStorage.getItem("astrolift.terminal.height.acme")).toBe("300");
  });

  it("clamps a drag above the minimum usable height", () => {
    const { wrapper } = renderLive();

    fireEvent.pointerDown(screen.getByLabelText("resizeHandle"), { clientY: 400 });
    fireEvent.pointerMove(window, { clientY: 0 });
    fireEvent.pointerUp(window);

    // A terminal dragged to nothing is unusable and unrecoverable — the
    // handle would have no height left to grab.
    expect(wrapper.style.height).toBe("192px");
  });

  it("restores the stored height on mount", () => {
    window.localStorage.setItem("astrolift.terminal.height.acme", "420");
    const { wrapper } = renderLive();
    expect(wrapper.style.height).toBe("420px");
  });

  it("ignores a stored height below the usable minimum", () => {
    // Guards against a bad write (or a hand-edited value) bricking the
    // terminal at 1px with no handle left to drag.
    window.localStorage.setItem("astrolift.terminal.height.acme", "12");
    const { wrapper } = renderLive();
    expect(wrapper.style.height).toBe("");
  });
});

describe("TerminalEmulator pop-out (#1246)", () => {
  it("targets the standalone route with the pod and container", () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);

    renderLive();
    fireEvent.click(screen.getByLabelText("popOut"));

    expect(open).toHaveBeenCalledTimes(1);
    const [url, name] = open.mock.calls[0];
    expect(url).toBe("/terminal/acme?pod=web-abc123&container=web");
    // Named after the target so a second click focuses the window already
    // showing this pod instead of stacking duplicates.
    expect(name).toBe("astrolift-shell-acme-web-abc123-web");
  });

  it("offers no pop-out, expand or resize once already standalone", () => {
    // A pop-out button inside a pop-out is a loop, and the OS window already
    // does resizing and full-screen.
    renderLive({ standalone: true });
    expect(screen.queryByLabelText("popOut")).toBeNull();
    expect(screen.queryByLabelText("expand")).toBeNull();
    expect(screen.queryByLabelText("resizeHandle")).toBeNull();
  });

  it("does not pin a height when standalone — the window sets it", () => {
    const { wrapper } = renderLive({ standalone: true, className: "flex-1" });
    expect(wrapper.className).not.toMatch(/(^|\s)h-96(\s|$)/);
  });
});
