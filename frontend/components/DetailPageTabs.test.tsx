import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DetailPageTabs, DetailTabRow } from "./DetailPageTabs";

const tab = (key: string, active = false) => ({
  key,
  label: key[0].toUpperCase() + key.slice(1),
  href: `/x/${key}`,
  active,
});

describe("DetailPageTabs", () => {
  it("marks only the active tab as the current page", () => {
    render(<DetailTabRow ariaLabel="Cluster tabs" tabs={[tab("overview", true), tab("health")]} />);
    const nav = screen.getByRole("navigation", { name: "Cluster tabs" });

    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(within(nav).getByRole("link", { name: "Health" })).not.toHaveAttribute("aria-current");
    expect(within(nav).getByRole("link", { name: "Health" })).toHaveAttribute("href", "/x/health");
  });

  it("renders one row of tabs named by function", () => {
    render(
      <DetailPageTabs ariaLabel="App pages" tabs={[tab("overview", true), tab("deployments")]} />
    );
    const nav = screen.getByRole("navigation", { name: "App pages" });

    expect(within(nav).getAllByRole("link")).toHaveLength(2);
  });

  it("lets a long label shrink inside its container rather than widen it", () => {
    render(<DetailTabRow ariaLabel="Tabs" tabs={[tab("a".repeat(80), true)]} />);

    expect(screen.getByRole("navigation", { name: "Tabs" })).toHaveClass(
      "min-w-0",
      "overflow-x-auto"
    );
  });
});
