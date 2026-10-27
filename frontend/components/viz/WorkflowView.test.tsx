import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithIntl } from "@/test/render-with-intl";
import { WorkflowView } from "./WorkflowView";
import type { WorkflowSnapshot } from "./core/workflow-model";
const snapshot: WorkflowSnapshot = {
  now: 0,
  trains: [],
  segments: [],
  lines: [
    {
      id: "factory",
      name: "Factory",
      stations: Array.from({ length: 13 }, (_, i) => ({
        id: String(i),
        name: `Stage ${i}`,
        kind: "stage",
      })),
    },
  ],
};
describe("workflow canvas", () => {
  it("expands the same long workflow with zoom controls and collapses its legend", () => {
    renderWithIntl(<WorkflowView snapshot={snapshot} value="transit" motion="reduced" />);
    expect(screen.getByText("Legend and symbols").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Zoom in" })).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Zoom in" }));
    expect(within(dialog).getByText("125%")).toBeVisible();
    expect(within(dialog).getByLabelText(/Workflow canvas/).firstElementChild).toHaveStyle({
      width: "2925px",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
