import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import { runStory } from "@/test/run-story";
import * as preview from "../../../.storybook/preview";
import * as source from "./ModelHostingSourcePanel.stories";
import * as deployment from "./SharedModelDeploymentScreen.stories";
import * as detail from "./SharedModelDetailScreen.stories";
import * as local from "./LocalModelImportPanel.stories";
import * as catalogue from "./HuggingFaceCataloguePanel.stories";
setProjectAnnotations(preview);
describe("model hosting portable stories", () => {
  it.each(
    Object.entries({
      ...composeStories(source),
      ...composeStories(catalogue),
      ...composeStories(local),
      ...composeStories(deployment),
      ...composeStories(detail),
    })
  )("%s", async (_name, Story) => {
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
