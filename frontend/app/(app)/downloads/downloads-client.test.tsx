import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DownloadsClient } from "./downloads-client";

describe("DownloadsClient private release contract", () => {
  it("offers authenticated native downloads without broken public installers", async () => {
    render(<DownloadsClient />);

    expect(await screen.findByText("Install channels")).toBeInTheDocument();
    expect(document.body.textContent).toContain("gh release download");
    expect(document.body.textContent).toContain("astro-checksums.txt");
    expect(document.body.textContent).not.toContain("brew install");
    expect(document.body.textContent).not.toContain("scoop install");
    expect(document.body.textContent).not.toContain("curl -fsSL");

    for (const link of screen.getAllByRole("link", { name: "Open release" })) {
      expect(link).toHaveAttribute(
        "href",
        "https://github.com/calliopeai/astrolift-cli/releases/latest"
      );
    }
  });
});
