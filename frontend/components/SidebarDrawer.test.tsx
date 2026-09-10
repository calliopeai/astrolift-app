import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarDrawer } from "./SidebarDrawer";

const sidebar = vi.hoisted(() => ({ state: "expanded" as "expanded" | "collapsed" }));

vi.mock("@/components/ui/sidebar", () => ({
  useSidebar: () => ({ state: sidebar.state }),
}));

function renderDrawer(storageKey = "test.drawer") {
  return render(
    <SidebarDrawer title="Platform" storageKey={storageKey}>
      <a href="https://example.test/build">Build</a>
    </SidebarDrawer>
  );
}

describe("SidebarDrawer", () => {
  beforeEach(() => {
    window.localStorage.clear();
    sidebar.state = "expanded";
  });

  it("opens by default", async () => {
    renderDrawer();
    expect(await screen.findByRole("link", { name: "Build" })).toBeInTheDocument();
  });

  it("closing hides its content and remembers the choice", async () => {
    const { unmount } = renderDrawer();

    fireEvent.click(screen.getByRole("button", { name: /platform/i }));
    expect(screen.queryByRole("link", { name: "Build" })).not.toBeInTheDocument();

    unmount();
    renderDrawer();
    expect(await screen.findByRole("button", { name: /platform/i })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
    expect(screen.queryByRole("link", { name: "Build" })).not.toBeInTheDocument();
  });

  it("each drawer keeps its own state", async () => {
    renderDrawer("drawer.a");
    fireEvent.click(screen.getByRole("button", { name: /platform/i }));

    render(
      <SidebarDrawer title="Workspace" storageKey="drawer.b">
        <a href="https://example.test/apps">Apps</a>
      </SidebarDrawer>
    );

    expect(await screen.findByRole("link", { name: "Apps" })).toBeInTheDocument();
  });

  it("an icon-collapsed rail still shows a closed drawer's destinations", async () => {
    // There are no headers to click in icon mode, so a closed drawer
    // would strip the rail of those icons with no way to get them back.
    window.localStorage.setItem("test.drawer", "closed");
    sidebar.state = "collapsed";

    renderDrawer();

    expect(await screen.findByRole("link", { name: "Build" })).toBeInTheDocument();
  });

  it("expanding again honours the operator's own choice", async () => {
    window.localStorage.setItem("test.drawer", "closed");
    renderDrawer();

    expect(await screen.findByRole("button", { name: /platform/i })).toHaveAttribute(
      "aria-expanded",
      "false"
    );
    expect(screen.queryByRole("link", { name: "Build" })).not.toBeInTheDocument();
  });

  it("survives storage being unavailable", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("access denied");
    });

    renderDrawer();

    expect(await screen.findByRole("link", { name: "Build" })).toBeInTheDocument();
    getItem.mockRestore();
  });
});
