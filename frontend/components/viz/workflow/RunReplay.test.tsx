import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { makeFeatureTimeline, makeTimeline } from "../core/workflow-model";

import { makeFanoutTimeline } from "./replay";

import { RunReplay } from "./RunReplay";

describe("RunReplay", () => {
  it("opens settled at the end under reduced motion, with the slowest-stage callout", () => {
    render(<RunReplay timeline={makeTimeline()} motion="reduced" />);
    const slider = screen.getByRole("slider");
    expect(slider.getAttribute("aria-valuenow")).toBe("913");
    expect(screen.getByRole("button", { name: /play replay/i })).toBeDisabled();
    expect(screen.getByText(/Slowest stage/).closest("p")!.textContent).toContain(
      "Approve took 6m 20s, 42%"
    );
    const exportButton = screen.getByRole("button", { name: /export/i });
    expect(exportButton).toBeEnabled();
    expect(exportButton).toHaveAttribute("aria-haspopup", "menu");
  });

  it("steps between stage boundaries with the arrow keys", () => {
    render(<RunReplay timeline={makeTimeline()} motion="reduced" />);
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "Home" });
    expect(slider.getAttribute("aria-valuenow")).toBe("0");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(slider.getAttribute("aria-valuenow")).toBe("94");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(slider.getAttribute("aria-valuenow")).toBe("306");
    expect(slider.getAttribute("aria-valuetext")).toContain("Scan");
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    expect(slider.getAttribute("aria-valuenow")).toBe("94");
  });

  it("shows who decided a gate once the playhead leaves it", () => {
    render(<RunReplay timeline={makeTimeline()} motion="reduced" />);
    expect(screen.getAllByText(/approved by reviewer@example.com/).length).toBeGreaterThan(0);
  });

  it("selects a stage with Enter and jumps to its start", () => {
    const onSelectStage = vi.fn();
    render(<RunReplay timeline={makeTimeline()} motion="reduced" onSelectStage={onSelectStage} />);
    fireEvent.keyDown(screen.getByRole("button", { name: /^Scan/ }), { key: "Enter" });
    expect(onSelectStage).toHaveBeenCalledWith("st2");
    expect(screen.getByRole("slider").getAttribute("aria-valuenow")).toBe("306");
  });

  it("groups a looping run by round and jumps to where a loop sent work back", () => {
    render(<RunReplay timeline={makeFeatureTimeline()} motion="reduced" />);
    const rounds = screen.getByRole("list", { name: "Rounds" }).querySelectorAll("li");
    expect(rounds).toHaveLength(4);
    expect(rounds[0].textContent).toBe("Round 1: Plan, Code, Test failed");
    expect(rounds[2].textContent).toContain("Review rejected");
    expect(rounds[3].getAttribute("aria-current")).toBe("step");
    expect(screen.getByRole("figure").getAttribute("aria-label")).toMatch(
      /4 rounds, sent back 3 times/
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: /Round 3: Test failed: 1 spec in checkout, sent back to Code/,
      })
    );
    expect(screen.getByRole("slider").getAttribute("aria-valuetext")).toContain("Code");
    expect(rounds[2].getAttribute("aria-current")).toBe("step");
  });

  it("draws a fan-out's branches as parallel bars", () => {
    render(<RunReplay timeline={makeFanoutTimeline()} motion="reduced" />);
    const bars = screen.getAllByRole("button", { name: /\(branch\)/ });
    expect(bars).toHaveLength(6);
    expect(bars[4].getAttribute("aria-label")).toBe("Source 5 (branch): failed, 1m 30s");
    expect(screen.queryByRole("list", { name: "Rounds" })).toBeNull();
  });
});
