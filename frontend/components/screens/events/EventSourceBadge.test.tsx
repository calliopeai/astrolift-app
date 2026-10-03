import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithIntl as render } from "@/test/render-with-intl";

import { PayloadPreview, SourceBadge } from "./EventSourceBadge";

describe("event JSON payloads", () => {
  it.each([0, false, "", ["first", "second"]])(
    "renders non-object JSON instead of losing its value: %j",
    (payload) => {
      render(<PayloadPreview payload={payload} />);
      expect(screen.getByText(JSON.stringify(payload))).toBeVisible();
    }
  );

  it.each([null, undefined, {}, []])("keeps absent or empty payloads empty: %j", (payload) => {
    render(<PayloadPreview payload={payload} />);
    expect(screen.getByText("—")).toBeVisible();
  });

  it.each([null, false, 0, "other-app", [{ app_slug: "other-app" }]])(
    "does not invent workload context from a non-record payload: %j",
    (payload) => {
      render(<SourceBadge resourceKind="workload" resourceId="worker" payload={payload} />);
      expect(screen.getByText("workload:worker")).toBeVisible();
      expect(screen.queryByRole("link")).not.toBeInTheDocument();
    }
  );

  it("retains exact record source values and encodes path segments", () => {
    render(
      <SourceBadge
        resourceKind="workload"
        resourceId="fallback"
        payload={{ app_slug: "checkout", workload_slug: "worker/blue" }}
      />
    );
    expect(screen.getByRole("link")).toHaveAttribute(
      "href",
      "/apps/checkout/workloads/worker%2Fblue"
    );
  });
});
