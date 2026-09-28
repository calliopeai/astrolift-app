import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { makeTimeline } from "../core/workflow-model";

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
    expect(screen.getByRole("button", { name: /export/i })).toHaveAttribute(
      "title",
      "GIF export coming"
    );
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
});
