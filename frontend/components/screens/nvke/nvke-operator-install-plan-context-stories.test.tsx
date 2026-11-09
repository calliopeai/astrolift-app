import { composeStories, setProjectAnnotations } from "@storybook/react";
import { expect, it } from "vitest";
import { runStory } from "@/test/run-story";
import * as preview from "@/.storybook/preview";
import * as module from "./NvkeOperatorInstallPlanContextPanel.stories";
import * as recovery from "./NvkeOperatorInstallPlanContextPanel.integration.stories";
import * as operation from "./NvkeOperationScreen.stories";
import * as api from "./NvkeOperatorInstallPlanContextPanel.api.stories";
setProjectAnnotations(preview);
it.each([
  ...Object.entries(composeStories(module)),
  ...Object.entries(composeStories(recovery)),
  ...Object.entries(composeStories(api)),
  ...Object.entries(composeStories(operation)).filter(([name]) =>
    name.startsWith("InstallPlanContext")
  ),
])("context story %s renders", async (_name, Story) => {
  const element = document.createElement("div");
  document.body.appendChild(element);
  await runStory(Story, element);
  expect(element.childElementCount).toBeGreaterThan(0);
  element.remove();
});
