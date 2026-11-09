import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import { runStory } from "@/test/run-story";
import * as preview from "@/.storybook/preview";
import * as module from "./NvkeOperatorInstallPlanTreePanel.stories";

import * as operation from "./NvkeOperationScreen.stories";
import * as api from "./NvkeOperatorInstallPlanTreePanel.api.stories";
import * as recovery from "./NvkeOperatorInstallPlanTreePanel.integration.stories";
setProjectAnnotations(preview);
const stories = [
  ...Object.entries(composeStories(module)),
  ...Object.entries(composeStories(operation)).filter(([name]) =>
    name.startsWith("InstallPlanTree")
  ),
  ...Object.entries(composeStories(recovery)),
  ...Object.entries(composeStories(api)),
];
describe("chart artifact stories", () => {
  it.each(stories)("%s renders", async (_name, Story) => {
    const element = document.createElement("div");
    document.body.appendChild(element);
    await runStory(Story, element);
    expect(element.childElementCount).toBeGreaterThan(0);
    element.remove();
  });
});
