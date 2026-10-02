import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DownloadsClient } from "./downloads-client";

describe("DownloadsClient public release contract", () => {
  it("offers anonymous native downloads and checksums without unpublished installers", async () => {
    render(<DownloadsClient />);

    expect(await screen.findByText("Install channels")).toBeInTheDocument();
    expect(document.body.textContent).toContain("curl -fL");
    expect(document.body.textContent).not.toContain("gh auth login");
    expect(document.body.textContent).not.toContain("Native archives are private");
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
