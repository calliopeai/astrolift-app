import { composeStories, setProjectAnnotations } from "@storybook/react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as preview from "../../../../.storybook/preview";
import { runStory } from "@/test/run-story";
import * as stories from "./NotificationQuietHoursPanel.stories";
import * as notificationStories from "./Notifications.stories";

setProjectAnnotations(preview);
const composed = composeStories(stories);
const notificationScreen = composeStories(notificationStories);
afterEach(cleanup);

describe("personal notification quiet hours", () => {
  it("renders quiet hours as its own notification settings section", async () => {
    render(<notificationScreen.QuietHoursSection />);
    expect(screen.getByLabelText("Time zone (IANA)")).toHaveValue("UTC");
    expect(screen.getByText("Quiet hours are active")).toBeVisible();
  });
  it.each(Object.entries(composed))("renders the %s story", async (_name, Story) => {
    const canvas = document.createElement("div");
    document.body.append(canvas);
    try {
      await runStory(Story, canvas);
    } finally {
      cleanup();
      canvas.remove();
    }
  });

  it("saves a cross-midnight window as minutes and the selected time zone", async () => {
    const onSave = vi.fn(async () => {});
    render(<composed.Default onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Time zone (IANA)"), {
      target: { value: "America/Costa_Rica" },
    });
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "23:30" } });
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "07:15" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({
        enabled: true,
        timezone: "America/Costa_Rica",
        startMinute: 1410,
        endMinute: 435,
      })
    );
  });

  it("refuses an equal start and end before saving", async () => {
    const onSave = vi.fn(async () => {});
    render(<composed.Default onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "22:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Start and end times must be different.")
    );
    expect(onSave).not.toHaveBeenCalled();
  });
});
