import { runStory } from "@/test/run-story";
import { cleanup } from "@testing-library/react";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../.storybook/preview";
import * as card from "./InstallManagedModelCard.stories";
import * as screen from "./SharedModelsScreen.stories";

// calliope-installer#446: the install's own model is listed read-only above
// the deployments, with replicas only when the install reports them.
setProjectAnnotations(preview);
const { WithInstallManagedModel, InstallManagedModelOnly } = composeStories(screen);
const stories = {
  ...composeStories(card),
  WithInstallManagedModel,
  InstallManagedModelOnly,
};

describe("install-managed model", () => {
  it.each(Object.entries(stories))("%s renders", async (_name, Story) => {
    const canvasElement = document.createElement("div");
    document.body.append(canvasElement);
    try {
      await runStory(Story, canvasElement);
      expect(canvasElement.querySelector("section[aria-label]")).not.toBeNull();
    } finally {
      cleanup();
      canvasElement.remove();
    }
  });

  it("says when the install does not report replicas, and counts them when it does", async () => {
    const { ReplicasNotReported, Reported } = composeStories(card);
    for (const [Story, text] of [
      [ReplicasNotReported, "Replicas not reported by the install"],
      [Reported, "2 replicas, one GPU each"],
    ] as const) {
      const canvasElement = document.createElement("div");
      document.body.append(canvasElement);
      try {
        await runStory(Story, canvasElement);
        expect(canvasElement.textContent).toContain(text);
      } finally {
        cleanup();
        canvasElement.remove();
      }
    }
  });
});
