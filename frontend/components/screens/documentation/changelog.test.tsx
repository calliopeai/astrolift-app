import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ChangelogScreen } from "./ChangelogScreen";

it("links canonical project history when versioned release notes are unavailable", () => {
  render(<ChangelogScreen />);
  expect(
    screen.getByText("Versioned release notes are not available in this dashboard.")
  ).toBeVisible();
  expect(screen.getByRole("link", { name: "repository changelog" })).toHaveAttribute(
    "href",
    "https://github.com/calliopeai/astrolift-app/blob/main/CHANGELOG.md"
  );
});

it("renders unknown release categories neutrally without interpreting them as failures", () => {
  render(
    <ChangelogScreen
      entries={[
        {
          version: "1.2.3",
          date: "2026-09-30",
          type: "maintenance",
          changes: ["Dependency maintenance"],
        },
      ]}
    />
  );
  expect(screen.getByText("maintenance")).toHaveAttribute("data-variant", "outline");
  expect(screen.getByText("Dependency maintenance")).toBeVisible();
});
