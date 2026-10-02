import { describe, expect, it } from "vitest";

import { previewHasAvailableBinding } from "./preview-binding";
import { PREVIEWS } from "./previews.fixtures";

describe("preview browser routing", () => {
  it("permits a validated live FK target", () =>
    expect(previewHasAvailableBinding(PREVIEWS[0])).toBe(true));
  it.each(["retired", "unavailable"])(
    "refuses a retained hostname with %s binding",
    (environmentStatus) => {
      expect(previewHasAvailableBinding({ ...PREVIEWS[0], environmentStatus })).toBe(false);
    }
  );
  it("refuses torn-down status and timestamps even with a live environment", () => {
    expect(previewHasAvailableBinding({ ...PREVIEWS[0], status: "torn_down" })).toBe(false);
    expect(previewHasAvailableBinding({ ...PREVIEWS[0], tornDownAt: "2026-10-02T01:00:00Z" })).toBe(
      false
    );
  });
  it.each(["previewId", "previewVersion", "appSlug", "namespace"])(
    "refuses a mismatched %s",
    (field) => {
      expect(
        previewHasAvailableBinding({
          ...PREVIEWS[0],
          environment: {
            ...PREVIEWS[0].environment!,
            [field]: field === "previewVersion" ? 0 : "another-target",
          },
        })
      ).toBe(false);
    }
  );
  it("refuses a missing FK even when a hostname exists", () =>
    expect(previewHasAvailableBinding({ ...PREVIEWS[0], environment: null })).toBe(false));
});
