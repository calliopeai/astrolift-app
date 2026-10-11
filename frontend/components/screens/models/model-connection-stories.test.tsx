import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import { runStory } from "@/test/run-story";
import * as preview from "../../../.storybook/preview";
import * as intake from "./ModelConnectionIntakePanel.stories";
import * as requests from "./ModelConnectionRequestsScreen.stories";
import * as request from "./ModelConnectionRequestScreen.stories";
import * as policy from "./ModelConnectionPolicyPanel.stories";
import * as requestsClient from "./ModelConnectionRequestsClient.stories";
import * as requestClient from "./ModelConnectionRequestClient.stories";
import * as policyClient from "./ModelConnectionPolicyClient.stories";
setProjectAnnotations(preview);
for (const [name, stories] of [
  ["intake", intake],
  ["requests", requests],
  ["request", request],
  ["policy", policy],
  ["requestsClient", requestsClient],
  ["requestClient", requestClient],
  ["policyClient", policyClient],
] as const)
  describe(name, () => {
    it.each(Object.entries(composeStories(stories)))("%s", async (_name, Story) => {
      const canvas = document.createElement("div");
      document.body.append(canvas);
      try {
        await runStory(Story as Parameters<typeof runStory>[0], canvas);
        expect(canvas.childElementCount).toBeGreaterThan(0);
      } finally {
        canvas.remove();
      }
    });
  });
