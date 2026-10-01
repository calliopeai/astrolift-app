import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, it, expect } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as detail from "./ClusterDetail.stories";
import * as list from "./ClustersList.stories";
setProjectAnnotations(preview);
for (const [name, stories] of [
  ["Cluster detail", detail],
  ["Clusters list", list],
] as const) {
  describe(`${name} portable stories`, () => {
    it.each(Object.entries(composeStories(stories)))("%s renders", async (_name, Story) => {
      const canvasElement = document.createElement("div");
      document.body.append(canvasElement);
      try {
        await Story.run({ canvasElement });
        expect(canvasElement.childElementCount).toBeGreaterThan(0);
      } finally {
        canvasElement.remove();
      }
    });
  });
}
