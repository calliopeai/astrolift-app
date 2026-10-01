import { cleanup, render } from "@testing-library/react";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as header from "./ClusterHeader.stories";
import * as tabs from "./ClusterTabs.stories";
setProjectAnnotations(preview);
for (const [name, stories] of [
  ["Cluster header", header],
  ["Cluster tabs", tabs],
] as const) {
  describe(`${name} portable stories`, () => {
    it.each(Object.entries(composeStories(stories)))("%s renders", async (_name, Story) => {
      const canvasElement = document.createElement("div");
      document.body.append(canvasElement);
      try {
        // Register story roots with the test renderer for deterministic unmount.
        await Story.run({ canvasElement, testingLibraryRender: render });
        expect(canvasElement.childElementCount).toBeGreaterThan(0);
      } finally {
        cleanup();
        canvasElement.remove();
      }
    });
  });
}
