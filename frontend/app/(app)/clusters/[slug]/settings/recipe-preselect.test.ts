import { describe, expect, it } from "vitest";

import { preselected } from "@/components/screens/clusters/settings/types";

// An install deletes every recipe release it is not given, and installs a
// second copy of any controller it is given that already runs (#2119).
describe("recipe preselection (#2119)", () => {
  it("keeps what the recipe installed checked, even when not a default", () => {
    expect(
      preselected({ defaultEnabled: false, installedByRecipe: true, runningOutsideRecipe: false })
    ).toBe(true);
  });

  it("never pre-checks a controller running outside the recipe", () => {
    expect(
      preselected({ defaultEnabled: true, installedByRecipe: false, runningOutsideRecipe: true })
    ).toBe(false);
  });

  it("otherwise follows the recipe's default", () => {
    expect(
      preselected({ defaultEnabled: true, installedByRecipe: false, runningOutsideRecipe: false })
    ).toBe(true);
    expect(
      preselected({ defaultEnabled: false, installedByRecipe: false, runningOutsideRecipe: false })
    ).toBe(false);
  });
});
