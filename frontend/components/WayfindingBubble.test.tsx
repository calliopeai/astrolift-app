import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useWayfinding } from "./use-wayfinding";
import { WayfindingBubble } from "./WayfindingBubble";

/** The bubble as the shell wires it. */
function Wired() {
  return <WayfindingBubble {...useWayfinding()} />;
}

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function mockAsk(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function open() {
  render(<Wired />);
  fireEvent.click(screen.getByRole("button", { name: /open help and wayfinding/i }));
}

function ask(question: string) {
  fireEvent.change(screen.getByRole("textbox", { name: /your question/i }), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
}

describe("WayfindingBubble", () => {
  it("starts collapsed so it never covers the page uninvited", () => {
    render(<Wired />);

    expect(screen.getByRole("button", { name: /open help and wayfinding/i })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("answers a question and offers the cited routes as links", async () => {
    const fetchMock = mockAsk({
      answer: "Open the app, then the Domains tab.",
      routes: ["/apps/hello"],
    });
    open();

    ask("where do I add a domain?");

    await waitFor(() => {
      expect(screen.getByText("Open the app, then the Domains tab.")).toBeTruthy();
    });
    const link = screen.getByRole("link", { name: "/apps/hello" });
    expect(link.getAttribute("href")).toBe("/apps/hello");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/agents/v1/wayfinding/ask/");
    expect(init.method).toBe("POST");
  });

  it("renders only the routes the server returned", async () => {
    // The server filters to what the viewer can reach; inventing a link here
    // from the answer text would put that guarantee back in the client,
    // where it is a suggestion rather than a guarantee.
    mockAsk({ answer: "See /administration/members for that.", routes: [] });
    open();

    ask("members?");

    await waitFor(() => {
      expect(screen.getByText(/See \/administration\/members for that\./)).toBeTruthy();
    });
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("says the install is not configured rather than failing generically", async () => {
    mockAsk({ error: "wayfinding not configured" }, 503);
    open();

    ask("anything");

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/can't reach a model/i);
    });
  });

  it("says when an operator turned it off (#2138)", async () => {
    mockAsk({ error: "wayfinding off", reason: "turned off for this install" }, 503);
    open();

    ask("anything");

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/turned off/i);
    });
  });

  it("surfaces the endpoint's own error text on a refusal", async () => {
    mockAsk({ error: "question must be 500 characters or fewer" }, 400);
    open();

    ask("x");

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/500 characters or fewer/);
    });
  });

  it("does not send an empty question", async () => {
    const fetchMock = mockAsk({ answer: "", routes: [] });
    open();

    fireEvent.change(screen.getByRole("textbox", { name: /your question/i }), {
      target: { value: "   " },
    });

    expect(screen.getByRole("button", { name: /^ask$/i }).hasAttribute("disabled")).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("closes on Escape", async () => {
    open();

    fireEvent.keyDown(window, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
