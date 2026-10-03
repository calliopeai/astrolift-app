import { runStory } from "@/test/run-story";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { cleanup } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../.storybook/preview";
import * as stories from "./SecretProposalDetail.stories";
import * as queueStories from "./SecretProposalsQueue.stories";
import * as summaryStories from "./SecretProposalsSummary.stories";

setProjectAnnotations(preview);
describe("Secret proposal portable stories", () => {
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

for (const [screen, fixtures] of Object.entries({
  Queue: composeStories(queueStories),
  Summary: composeStories(summaryStories),
})) {
  describe(`Secret proposal ${screen} portable stories`, () => {
    it.each(Object.entries(fixtures))("%s renders", async (_name, Story) => {
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
