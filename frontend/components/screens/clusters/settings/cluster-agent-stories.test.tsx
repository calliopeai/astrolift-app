import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./ClusterAgent.stories";
import * as settingsStories from "./ClusterSettings.stories";
setProjectAnnotations(preview);
describe("Cluster agent portable stories", () => {
  it.each([
    ...Object.entries(composeStories(stories)).map(
      ([name, story]) => [`Agent/${name}`, story] as const
    ),
    ...Object.entries(composeStories(settingsStories)).map(
      ([name, story]) => [`Settings/${name}`, story] as const
    ),
  ])("%s renders", async (_name, Story) => {
    const canvasElement = document.createElement("div");
    document.body.appendChild(canvasElement);
    try {
      await Story.run({ canvasElement });
      expect(canvasElement.childElementCount).toBeGreaterThan(0);
    } finally {
      canvasElement.remove();
    }
  });
});
