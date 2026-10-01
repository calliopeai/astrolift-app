import { fireEvent, screen } from "@testing-library/react";
import { renderWithIntl as render } from "@/test/render-with-intl";
import { describe, expect, it, vi } from "vitest";

import { BOTH, homeProps, ONLY_APPS } from "./fixtures";
import { HOME_QUESTION, HomeScreen } from "./HomeScreen";
import type { HomePanelDef } from "./registry";

/** A panel that records it was mounted, standing in for one that fetches. */
function spyPanels(panels: HomePanelDef[], mounted: string[]): HomePanelDef[] {
  return panels.map((p) => ({
    ...p,
    component: ({ panel }) => {
      mounted.push(panel.key);
      return <section aria-label={panel.title} />;
    },
  }));
}

describe("HomeScreen", () => {
  it("mounts only the layout's visible panels, in order", () => {
    const mounted: string[] = [];
    const props = homeProps(ONLY_APPS, "builder");
    render(
      <HomeScreen {...props} panels={spyPanels(props.panels, mounted)} onLayoutChange={() => {}} />
    );
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home · Apps");
    expect(mounted).toEqual([
      "waiting",
      "failing",
      "my-apps",
      "recent-deployments",
      "traffic-errors",
    ]);
  });

  it("asks the one question first and mounts no panel until it is answered", () => {
    const mounted: string[] = [];
    const onLayoutChange = vi.fn();
    const props = homeProps(BOTH);
    render(
      <HomeScreen
        {...props}
        panels={spyPanels(props.panels, mounted)}
        firstSignIn
        onLayoutChange={onLayoutChange}
      />
    );
    expect(screen.getByRole("group", { name: HOME_QUESTION })).toBeTruthy();
    expect(mounted).toEqual([]);
    expect((screen.getByRole("radio", { name: /Builder/ }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /^Agents/ }));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onLayoutChange).toHaveBeenCalledWith("agents");
  });
});
