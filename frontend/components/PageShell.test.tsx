import { act, within } from "@testing-library/react";
import Link from "next/link";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

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
