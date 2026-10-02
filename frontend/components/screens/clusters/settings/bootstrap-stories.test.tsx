import { runStory } from "@/test/run-story";
import { cleanup } from "@testing-library/react";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as recipes from "./BootstrapPlan.stories";
import * as settings from "./ClusterSettings.stories";

setProjectAnnotations(preview);
for (const [name, stories] of [
  ["Bootstrap recipe", recipes],
  ["Cluster settings/history", settings],
] as const) {
  describe(`${name} portable stories`, () => {
    it.each(Object.entries(composeStories(stories)))("%s renders", async (_name, Story) => {
      const canvasElement = document.createElement("div");
      document.body.append(canvasElement);
      try {
        await runStory(Story, canvasElement);
        expect(canvasElement.childElementCount).toBeGreaterThan(0);
      } finally {
        cleanup();
        canvasElement.remove();
      }
    });
  });
}
