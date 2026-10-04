import { composeStories, setProjectAnnotations } from "@storybook/react";
import { cleanup, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { runStory } from "@/test/run-story";
import en from "@/messages/en.json";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./FeaturesScreen.stories";

setProjectAnnotations(preview);
const { AcceptedRefreshFailed, UncertainWrite, Error, SpanishWidth768 } = composeStories(stories);

describe("feature write recovery portable stories", () => {
  it.each([AcceptedRefreshFailed, UncertainWrite])(
    "%s keeps write controls read-only",
    async (Story) => {
      const canvas = document.createElement("div");
      document.body.append(canvas);
      try {
        await runStory(Story, canvas);
        const page = within(canvas);
        expect(page.getByRole("status")).toBeVisible();
        expect(
          page.getByRole("button", { name: en.administration.features.refresh })
        ).toBeEnabled();
        for (const control of page.getAllByRole("switch")) expect(control).toBeDisabled();
      } finally {
        cleanup();
        canvas.remove();
      }
    }
  );
  it.each([Error, SpanishWidth768])("%s preserves accessible confirmation", async (Story) => {
    const canvas = document.createElement("div");
    document.body.append(canvas);
    try {
      await runStory(Story, canvas);
      expect(within(document.body).getByRole("alertdialog")).toBeVisible();
    } finally {
      cleanup();
      canvas.remove();
    }
  });
});
