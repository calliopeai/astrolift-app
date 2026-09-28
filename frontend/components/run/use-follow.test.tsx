import { act, fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { describe, expect, it } from "vitest";

import { makeLines } from "./fixtures";
import { LogView } from "./LogView";
import { END_SLACK, followAfterScroll, isAtEnd } from "./use-follow";

const at = (scrollTop: number, scrollHeight = 1000, clientHeight = 200) => ({
  scrollTop,
  scrollHeight,
  clientHeight,
});

describe("isAtEnd", () => {
  it("counts the last END_SLACK px as the end", () => {
    expect(isAtEnd(at(800))).toBe(true);
    expect(isAtEnd(at(800 - END_SLACK))).toBe(true);
    expect(isAtEnd(at(800 - END_SLACK - 1))).toBe(false);
  });
  it("a pane shorter than its frame is at the end", () => {
    expect(isAtEnd(at(0, 100, 200))).toBe(true);
  });
});

describe("followAfterScroll", () => {
  it("stops following when the reader scrolls up", () => {
    expect(followAfterScroll(true, 800, at(600))).toBe(false);
  });
  it("keeps following on a small jitter inside the end slack", () => {
    expect(followAfterScroll(true, 800, at(790))).toBe(true);
  });
  it("follows again on reaching the end", () => {
    expect(followAfterScroll(false, 600, at(800))).toBe(true);
  });
  it("stays stopped on a scroll down short of the end", () => {
    expect(followAfterScroll(false, 300, at(500))).toBe(false);
  });
  it("never starts following on a scroll down short of the end", () => {
    expect(followAfterScroll(false, 0, at(10))).toBe(false);
  });
});

/** jsdom has no layout: give the pane a fixed geometry. */
function sizePane(pane: HTMLElement, scrollHeight: () => number, clientHeight = 200) {
  Object.defineProperty(pane, "scrollHeight", { configurable: true, get: scrollHeight });
  Object.defineProperty(pane, "clientHeight", { configurable: true, get: () => clientHeight });
}

describe("LogView follow", () => {
  function Harness({ count }: { count: number }) {
    const lines = React.useMemo(() => makeLines(count), [count]);
    return <LogView lines={lines} />;
  }

  it("pins to the end, stops on scroll up, and Jump to latest follows again", () => {
    const { rerender } = render(<Harness count={50} />);
    const pane = screen.getByRole("log");
    let height = 1000;
    sizePane(pane, () => height);

    // Growth while following pins to the end.
    height = 1200;
    rerender(<Harness count={60} />);
    expect(pane.scrollTop).toBe(1200);
    expect(screen.getByRole("button", { name: "Follow" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: /Jump to latest/ })).toBeNull();

    // The reader scrolls up: following stops and the jump appears.
    act(() => {
      pane.scrollTop = 400;
      fireEvent.scroll(pane);
    });
    expect(screen.getByRole("button", { name: "Follow" })).toHaveAttribute("aria-pressed", "false");

    // New lines do not move the pane, and are counted.
    height = 1400;
    rerender(<Harness count={72} />);
    expect(pane.scrollTop).toBe(400);
    const jump = screen.getByRole("button", { name: /Jump to latest/ });
    expect(jump).toHaveTextContent("12 new");

    fireEvent.click(jump);
    expect(pane.scrollTop).toBe(1400);
    expect(screen.getByRole("button", { name: "Follow" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: /Jump to latest/ })).toBeNull();
  });

  it("the Follow toggle turns following off and back on", () => {
    render(<Harness count={10} />);
    const follow = screen.getByRole("button", { name: "Follow" });
    fireEvent.click(follow);
    expect(follow).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /Jump to latest/ })).toBeInTheDocument();
    fireEvent.click(follow);
    expect(follow).toHaveAttribute("aria-pressed", "true");
  });
});
