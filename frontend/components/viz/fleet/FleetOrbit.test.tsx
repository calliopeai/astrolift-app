import { act, fireEvent, render, screen } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { makeFleet } from "../core/fleet-model";
import { MOTION_CLASS } from "../core/semantics";

import { FleetOrbit } from "./FleetOrbit";

describe("FleetOrbit", () => {
  const fleet = makeFleet({ clusters: 2, agentsPerCluster: 5, failing: 0.4, seed: 5 });
  const failing = fleet.agents.filter((a) => a.health === "failing").length;

  it("summarises the fleet and marks the motion mode", () => {
    render(<FleetOrbit snapshot={fleet} motion="full" />);
    const root = screen.getByRole("group");
    expect(root).toHaveAttribute("data-motion", "full");
    expect(root.getAttribute("aria-label")).toContain(
      `10 agents across 2 clusters, ${failing} failing`
    );
  });

  it("hydrates server-rendered cluster titles with their accessible region text", async () => {
    const tree = <FleetOrbit snapshot={fleet} motion="reduced" />;
    const container = document.createElement("div");
    container.innerHTML = renderToString(tree);
    document.body.append(container);
    const svg = container.querySelector("svg");
    const recover = vi.fn();
    let root!: ReturnType<typeof hydrateRoot>;
    try {
      await act(async () => {
        root = hydrateRoot(container, tree, { onRecoverableError: recover });
      });
      expect(recover).not.toHaveBeenCalled();
      expect(container.querySelector("svg")).toBe(svg);
      expect([...container.querySelectorAll("title")].map((title) => title.textContent)).toContain(
        `${fleet.clusters[0].name} (${fleet.clusters[0].region})`
      );
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  it("selects an agent with Enter", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <FleetOrbit snapshot={fleet} motion="full" onSelectAgent={onSelect} />
    );
    const sat = container.querySelector('[role="button"]') as SVGGElement;
    expect(sat).toHaveAttribute("tabindex", "0");
    fireEvent.keyDown(sat, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(fleet.agents[0].id);
  });

  it("flickers failing agents in full motion and rings them when reduced", () => {
    expect(failing).toBeGreaterThan(0);
    const full = render(<FleetOrbit snapshot={fleet} motion="full" />);
    expect(full.container.querySelectorAll(`.${MOTION_CLASS.flicker}`)).toHaveLength(failing);
    full.unmount();
    const still = render(<FleetOrbit snapshot={fleet} motion="reduced" />);
    expect(still.container.querySelectorAll(`.${MOTION_CLASS.flicker}`)).toHaveLength(0);
  });
});
