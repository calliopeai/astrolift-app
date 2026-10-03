import { runStory } from "@/test/run-story";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./CentralAuth.stories";
setProjectAnnotations(preview);
describe("Central auth portable stories", () => {
  it.each(Object.entries(composeStories(stories)))("%s renders", async (_name, Story) => {
    const canvasElement = document.createElement("div");
    document.body.append(canvasElement);
    try {
      await runStory(Story, canvasElement);
      expect(canvasElement.childElementCount).toBeGreaterThan(0);
    } finally {
      canvasElement.remove();
    }
  });
});
