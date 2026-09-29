import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeWorkflows, type WorkflowSnapshot } from "../core/workflow-model";

import { WorkflowTransit } from "./WorkflowTransit";

const shaped = makeWorkflows({ lines: 0, trainsPerLine: 0, shapes: true });
const [feature, , , close] = shaped.lines;

function withTrains(trains: WorkflowSnapshot["trains"]): WorkflowSnapshot {
  return { ...shaped, trains };
}

const base = { label: "#5301", progress: 0, state: "moving" as const, startedAt: 0 };

describe("WorkflowTransit", () => {
  it("names a station's round against its bound, in words", () => {
    const [test] = feature.loops!;
    render(
      <WorkflowTransit
        motion="reduced"
        snapshot={withTrains([
          {
            ...base,
            id: "a",
            lineId: feature.id,
            at: 2,
            state: "failed",
            loopRounds: { [test.id]: 2 },
          },
        ])}
      />
    );
    const station = screen.getByRole("img", {
      name: /^Feature delivery: Test, round 3 of 5, failed/,
    });
    expect(station.getAttribute("aria-label")).toContain(
      "Back to Code when Test fails, at most 5 rounds"
    );
    expect(screen.getByRole("group").getAttribute("aria-label")).toMatch(/1 failed/);
  });

  it("says a run riding a return track is being sent back", () => {
    const [test] = feature.loops!;
    render(
      <WorkflowTransit
        motion="reduced"
        onSelectRun={() => {}}
        snapshot={withTrains([
          { ...base, id: "a", lineId: feature.id, at: 2, onLoopId: test.id, loopProgress: 0.5 },
        ])}
      />
    );
    expect(
      screen.getByRole("button", {
        name: /sent back to Code because Test failed, round 2 of 5 next/,
      })
    ).toBeTruthy();
  });

  it("opens a nested station's child line in place and closes it from the way back", () => {
    render(<WorkflowTransit motion="reduced" snapshot={withTrains([])} />);
    const nested = screen.getByRole("button", { name: /^Quarterly close: Reconcile/ });
    expect(nested).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("↳ Reconcile")).toBeNull();
    fireEvent.keyDown(nested, { key: "Enter" });
    expect(nested).toHaveAttribute("aria-expanded", "true");
    const back = screen.getByRole("button", { name: `Back to ${close.name}: close Reconcile` });
    fireEvent.click(back);
    expect(nested).toHaveAttribute("aria-expanded", "false");
    expect(document.activeElement).toBe(nested);
  });
});
