import { fireEvent, render, screen } from "@testing-library/react";
import * as React from "react";
import { describe, expect, it } from "vitest";

import { SettingsPage, type SettingsSectionSpec } from "./SettingsPage";
import { sectionHref, useLocalSettingsSection } from "./use-settings-section";

const SECTIONS: SettingsSectionSpec[] = [
  { id: "config", title: "Configuration", content: <p>config editor</p> },
  { id: "console", title: "Console", content: <p>shell console</p> },
];

function Single({ initial = null }: { initial?: string | null }) {
  const section = useLocalSettingsSection(initial);
  return (
    <SettingsPage
      single={section}
      sections={SECTIONS}
      dangerZone={<p>delete it</p>}
      restrictedMode="show"
    />
  );
}

const nav = () => screen.getByRole("navigation", { name: "Settings sections" });

describe("SettingsPage single-section mode", () => {
  it("mounts only the section named by ?section=", () => {
    render(<Single initial="console" />);
    expect(screen.getByText("shell console")).toBeInTheDocument();
    expect(screen.queryByText("config editor")).toBeNull();
    expect(screen.queryByText("delete it")).toBeNull();
  });

  it("falls back to the first section for an absent or unknown id", () => {
    render(<Single initial="nope" />);
    expect(screen.getByText("config editor")).toBeInTheDocument();
    expect(screen.queryByText("shell console")).toBeNull();
  });

  it("lists every section, linked by ?section=, and switches on click", () => {
    render(<Single />);
    const links = Array.from(nav().querySelectorAll("ul a"));
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "?section=config",
      "?section=console",
      "?section=danger-zone",
    ]);
    fireEvent.click(links[2]);
    expect(screen.getByText("delete it")).toBeInTheDocument();
    expect(screen.queryByText("config editor")).toBeNull();
    expect(links[2]).toHaveAttribute("aria-current", "page");
  });

  it("keeps the rest of the query in the section link", () => {
    expect(sectionHref("/apps/checkout/settings", "env=prod&section=a", "console")).toBe(
      "/apps/checkout/settings?env=prod&section=console"
    );
  });
});

describe("SettingsPage default mode", () => {
  it("mounts every section", () => {
    render(<SettingsPage sections={SECTIONS} restrictedMode="show" />);
    expect(screen.getByText("config editor")).toBeInTheDocument();
    expect(screen.getByText("shell console")).toBeInTheDocument();
  });
});
