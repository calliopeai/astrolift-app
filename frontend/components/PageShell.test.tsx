import { act, render, screen, within } from "@testing-library/react";
import Link from "next/link";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AppChromeProvider } from "@/lib/app-chrome-context";

import { PageShell } from "./PageShell";

describe("PageShell server-rendered descriptions", () => {
  it.each([false, true])(
    "hydrates breadcrumb navigation without replacing its header (collapsible=%s)",
    async (collapsibleHeader) => {
      const tree = (
        <PageShell
          title="Project"
          collapsibleHeader={collapsibleHeader}
          description={
            <nav aria-label="Project breadcrumb">
              <ol>
                <li>
                  <Link href="/projects">Projects</Link>
                </li>
              </ol>
            </nav>
          }
        >
          <p>Workloads</p>
        </PageShell>
      );
      const container = document.createElement("div");
      container.innerHTML = renderToString(tree);
      document.body.append(container);
      const header = container.querySelector("header");
      const recover = vi.fn();
      let root!: ReturnType<typeof hydrateRoot>;
      try {
        await act(async () => {
          root = hydrateRoot(container, tree, { onRecoverableError: recover });
        });
        expect(recover).not.toHaveBeenCalled();
        expect(container.querySelector("header")).toBe(header);
        expect(
          within(container).getByRole("navigation", { name: "Project breadcrumb" })
        ).toBeVisible();
        expect(within(container).getByRole("link", { name: "Projects" })).toHaveAttribute(
          "href",
          "/projects"
        );
      } finally {
        await act(async () => root.unmount());
        container.remove();
      }
    }
  );
});

it("preserves the app frame toolbar without duplicating its title", () => {
  render(
    <AppChromeProvider framed>
      <PageShell
        title="Child heading"
        description="Environment settings"
        actions={<button>Save</button>}
      >
        <p>Settings body</p>
      </PageShell>
    </AppChromeProvider>
  );
  expect(screen.queryByRole("heading", { name: "Child heading" })).not.toBeInTheDocument();
  expect(screen.getByText("Environment settings")).toBeVisible();
  expect(screen.getByRole("button", { name: "Save" })).toBeVisible();
  expect(screen.getByText("Settings body")).toBeVisible();
});

it("lets the agent shell own its chrome while retaining the child page body", () => {
  render(
    <AppChromeProvider basePath="/agents" agentShell>
      <PageShell title="Child heading" description="Child toolbar" actions={<button>Save</button>}>
        <p>Agent platform body</p>
      </PageShell>
    </AppChromeProvider>
  );
  expect(screen.queryByRole("heading", { name: "Child heading" })).not.toBeInTheDocument();
  expect(screen.queryByText("Child toolbar")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  expect(screen.getByText("Agent platform body")).toBeVisible();
});
