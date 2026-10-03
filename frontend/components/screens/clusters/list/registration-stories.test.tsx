import { runStory } from "@/test/run-story";
import { cleanup } from "@testing-library/react";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./RegisterClusterPage.stories";
setProjectAnnotations(preview);
describe("cluster registration portable stories", () => {
  it.each(Object.entries(composeStories(stories)))(
    "%s renders and completes its interactions",
    async (_name, Story) => {
      const canvasElement = document.createElement("div");
      document.body.append(canvasElement);
      try {
        await runStory(Story, canvasElement);
        expect(canvasElement.childElementCount).toBeGreaterThan(0);
      } finally {
        cleanup();
        canvasElement.remove();
      }
    }
  );
});
