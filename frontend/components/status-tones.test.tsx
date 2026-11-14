import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DeploymentStatusPill } from "@/components/DeploymentStatusPill";
import { StatusDot } from "@/components/StatusDot";
import { renderWithIntl as render } from "@/test/render-with-intl";

import { DOT_TONE, PILL_TONE } from "@calliopelabs/ui";

describe("status tones", () => {
  it("never colour a status with the selectable accent", () => {
    for (const cls of [...Object.values(DOT_TONE), ...Object.values(PILL_TONE)]) {
      expect(cls).not.toContain("brand-primary");
    }
  });

  it("colour a healthy dot and a running deployment with the success token", () => {
    const { container } = render(<StatusDot status="ok" />);
    expect(container.querySelector("[aria-hidden]")).toHaveClass("bg-success");

    render(<DeploymentStatusPill status="running" />);
    expect(screen.getByLabelText("Status: Running")).toHaveClass("text-success-fg");
  });

  it("pulse only the dot of in-flight states, and not under reduced motion", () => {
    render(<DeploymentStatusPill status="deploying" />);
    const deploying = screen.getByLabelText("Status: Deploying");
    expect(deploying).not.toHaveClass("animate-pulse");
    expect(deploying.querySelector("[aria-hidden]")).toHaveClass(
      "animate-pulse",
      "motion-reduce:animate-none"
    );

    render(<DeploymentStatusPill status="failed" />);
    expect(screen.getByLabelText("Status: Failed").querySelector("[aria-hidden]")).not.toHaveClass(
      "animate-pulse"
    );
  });
});
