import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import { runStory } from "@/test/run-story";
import * as preview from "@/.storybook/preview";
import * as module from "./NvkeOperatorInstallPlanChartsPanel.stories";

setProjectAnnotations(preview);
const stories = Object.entries(composeStories(module));
describe("chart artifact stories", () => {
  it.each(stories)("%s renders", async (_name, Story) => {
    const element = document.createElement("div");
    document.body.appendChild(element);
    await runStory(Story, element);
    expect(element.childElementCount).toBeGreaterThan(0);
    element.remove();
  });
});
