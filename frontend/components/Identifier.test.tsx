import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Identifier, shortIdentifier } from "./Identifier";

const SHA = "1112015d8a9b7c6e5f4d3c2b1a0f9e8d7c6b5a49";
const ARN = "arn:aws:acm:us-west-2:123456789012:certificate/0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b";

describe("shortIdentifier", () => {
  it("keeps eight characters of a SHA", () => {
    expect(shortIdentifier(SHA, "sha")).toBe("1112015d");
  });

  it("keeps a digest's algorithm", () => {
    expect(shortIdentifier(`sha256:${"ab".repeat(32)}`, "digest")).toBe("sha256:abababababab…");
  });

  it("keeps an ARN's service and resource", () => {
    expect(shortIdentifier(ARN, "arn")).toBe("acm:…:certificate/…0c1d2e3f4a5b");
    expect(shortIdentifier("arn:aws:s3:::bucket", "arn")).toBe("s3:…:bucket");
  });

  it("shows a URL's host and path", () => {
    expect(shortIdentifier("https://astro.example.com/apps/checkout", "url")).toBe(
      "astro.example.com/apps/checkout"
    );
  });

  it("leaves short values alone", () => {
    expect(shortIdentifier("abc123", "id")).toBe("abc123");
  });
});

describe("Identifier", () => {
  it("shows the short form with the full value in the tooltip, and copies the full value", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<Identifier value={SHA} kind="sha" />);
    const button = screen.getByRole("button", { name: `Copy ${SHA}` });

    expect(button).toHaveTextContent("1112015d");
    expect(button).toHaveAttribute("title", SHA);
    fireEvent.click(button);
    expect(writeText).toHaveBeenCalledWith(SHA);
  });

  it("wraps anywhere in its full form so a long value never widens its frame", () => {
    render(<Identifier value={ARN} kind="arn" form="full" copyable={false} />);

    expect(screen.getByText(ARN)).toHaveClass("[overflow-wrap:anywhere]", "min-w-0");
  });
});
