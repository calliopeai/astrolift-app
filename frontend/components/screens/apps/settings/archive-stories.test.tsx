import { composeStories, setProjectAnnotations } from "@storybook/react";
import { cleanup, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./ArchiveApp.stories";
setProjectAnnotations(preview);
describe("Archive portable stories", () => {
  it.each(Object.entries(composeStories(stories)))("%s renders", async (_name, Story) => {
    const canvasElement = document.createElement("div");
    document.body.append(canvasElement);
    try {
      await Story.run({ canvasElement, testingLibraryRender: render });
      expect(canvasElement.childElementCount).toBeGreaterThan(0);
    } finally {
      cleanup();
      canvasElement.remove();
    }
  });
});
