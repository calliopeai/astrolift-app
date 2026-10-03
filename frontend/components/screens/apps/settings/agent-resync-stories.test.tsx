import { runStory } from "@/test/run-story";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { cleanup } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./ResyncSource.stories";
setProjectAnnotations(preview);
describe("Agent source resync portable stories", () => {
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
