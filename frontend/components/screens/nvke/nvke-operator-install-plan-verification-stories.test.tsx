import { composeStories, setProjectAnnotations } from "@storybook/react";
import { expect, it } from "vitest";
import { runStory } from "@/test/run-story";
import * as preview from "@/.storybook/preview";
import * as stories from "./NvkeOperatorInstallPlanVerificationPanel.stories";
import * as recovery from "./NvkeOperatorInstallPlanVerificationPanel.integration.stories";
import * as operation from "./NvkeOperationScreen.stories";

setProjectAnnotations(preview);
it.each([
  ...Object.entries(composeStories(stories)),
  ...Object.entries(composeStories(recovery)),
  ...Object.entries(composeStories(operation)).filter(([name]) =>
    name.startsWith("InstallPlanVerification")
  ),
])("verification story %s renders", async (_name, Story) => {
  const element = document.createElement("div");
  document.body.appendChild(element);
  await runStory(Story, element);
  expect(element.childElementCount).toBeGreaterThan(0);
  element.remove();
});
