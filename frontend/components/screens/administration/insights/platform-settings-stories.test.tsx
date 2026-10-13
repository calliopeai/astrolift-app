import { composeStories, setProjectAnnotations } from "@storybook/react";
import { cleanup, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { runStory } from "@/test/run-story";
import * as preview from "../../../../.storybook/preview";
import * as stories from "./PlatformSettingsScreen.stories";

setProjectAnnotations(preview);
const { Full, ConfirmsSensitive, QueryError, Saving, SecretStores } = composeStories(stories);

async function render(Story: Parameters<typeof runStory>[0]) {
  const canvas = document.createElement("div");
  document.body.append(canvas);
  await runStory(Story, canvas);
  return { page: within(canvas), done: () => (cleanup(), canvas.remove()) };
}

describe("platform settings stories (#1861)", () => {
  it("edits what is editable and says why the rest is not", async () => {
    const { page, done } = await render(Full);
    try {
      expect(page.getByRole("textbox", { name: "Value of AGENT_POD_UID" })).toBeEnabled();
      expect(page.queryByRole("textbox", { name: "Value of TEMPORAL_ENABLED" })).toBeNull();
      expect(page.getByText(/switch it under Administration > Features/)).toBeVisible();
      expect(page.getByText(/never sent to a browser/)).toBeVisible();
      expect(page.getByRole("button", { name: "Reset to empty" })).toBeVisible();
    } finally {
      done();
    }
  });

  it("confirms a sensitive change before saving", async () => {
    const { done } = await render(ConfirmsSensitive);
    try {
      expect(within(document.body).getByRole("alertdialog")).toBeVisible();
    } finally {
      done();
    }
  });

  it("locks the other settings while one saves, and shows a load failure", async () => {
    const saving = await render(Saving);
    try {
      expect(saving.page.getByRole("textbox", { name: "Value of AGENT_POD_UID" })).toBeDisabled();
    } finally {
      saving.done();
    }
    const failed = await render(QueryError);
    try {
      expect(failed.page.getByText("only the platform operator may do this")).toBeVisible();
    } finally {
      failed.done();
    }
  });

  it("lists secret stores and one organization's refs by presence", async () => {
    const { page, done } = await render(SecretStores);
    try {
      expect(page.getByText("conflict-astrolift")).toBeVisible();
      expect(page.getByText("Has a value")).toBeVisible();
      expect(page.getByText("No value set")).toBeVisible();
      expect(page.getByText("outside this spec's owner namespace")).toBeVisible();
      const checks = page.getAllByRole("button", { name: "Check secrets" });
      expect(checks[1]).toBeDisabled();
    } finally {
      done();
    }
  });
});
