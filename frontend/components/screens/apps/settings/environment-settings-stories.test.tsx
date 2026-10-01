import { composeStories, setProjectAnnotations } from "@storybook/react";
import { render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import preview from "../../../../.storybook/preview";
import * as stories from "./EnvironmentSettings.stories";

setProjectAnnotations(preview);
const composed = composeStories(stories);
describe("Portable environment override stories", () => {
  it.each(Object.entries(composed))(
    "renders %s with the real project decorators",
    (name, Story) => {
      render(<Story />);
      if (name === "NoEnvironments") expect(screen.queryByText("Environment overrides")).toBeNull();
      else if (name === "SpanishWidth768")
        expect(screen.getByText("Ajustes por entorno")).toBeInTheDocument();
      else expect(screen.getByText("Environment overrides")).toBeInTheDocument();
    }
  );
  it("keeps only the existing clearing row disabled", () => {
    render(<composed.Clearing />);
    expect(screen.getByRole("button", { name: "Clear replicas override" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Clear memory_limit override" })).toBeEnabled();
  });
  it("keeps cached data visible while failed reads disable writes and offer retry", async () => {
    render(<composed.CachedReadError />);
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_ENV_READ_DIAGNOSTIC");
    expect(screen.getByRole("button", { name: "Add override" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  });
  it("displays declared loading without a confirmed empty claim", () => {
    render(<composed.Reading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading environment overrides");
    expect(screen.getByRole("button", { name: "Add override" })).toBeDisabled();
  });
});
