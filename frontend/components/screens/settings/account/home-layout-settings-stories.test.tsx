import { runStory } from "@/test/run-story";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";

import * as preview from "../../../../.storybook/preview";
import * as module from "./HomeLayoutSettings.stories";

setProjectAnnotations(preview);
const stories = Object.entries(composeStories(module));
describe("Home layout settings portable stories", () => {
  it.each(stories)("%s renders", async (_name, Story) => {
    const canvasElement = document.createElement("div");
    document.body.appendChild(canvasElement);
    try {
      await runStory(Story, canvasElement);
      expect(canvasElement.childElementCount).toBeGreaterThan(0);
    } finally {
      canvasElement.remove();
    }
  });
});
