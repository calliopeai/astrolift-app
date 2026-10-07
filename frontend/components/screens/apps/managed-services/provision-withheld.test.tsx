import { runStory } from "@/test/run-story";
import { cleanup } from "@testing-library/react";
import { composeStories, setProjectAnnotations } from "@storybook/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./ManagedServicesScreen.stories";

// calliope-installer#447: with databases withheld the sheet shows the reason
// and only the in-cluster variant can be submitted; the play asserts both.
setProjectAnnotations(preview);
const { ProvisionDatabasesWithheld, Provision } = composeStories(stories);

describe("provision sheet with databases withheld", () => {
  it.each([
    ["withheld", ProvisionDatabasesWithheld, true],
    ["unset", Provision, false],
  ] as const)("%s", async (_name, Story, shown) => {
    const canvasElement = document.createElement("div");
    document.body.append(canvasElement);
    try {
      await runStory(Story, canvasElement);
      expect(document.body.textContent?.includes("Databases are withheld")).toBe(shown);
    } finally {
      cleanup();
      canvasElement.remove();
    }
  });
});
