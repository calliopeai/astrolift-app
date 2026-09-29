import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoxesTab } from "./boxes-tab";

/**
 * The button (#128).
 *
 * The interesting claims are the ones an operator would notice being wrong:
 * pressing it sends the ensure mutation with the idle timeout that was
 * chosen (a box that quietly defaults to never-reap is a node bill), and a
 * live box hands back the exact command to attach to it rather than making
 * the operator reconstruct a tmux invocation the platform owns.
 */

const state = vi.hoisted(() => ({
  boxes: [] as Record<string, unknown>[],
  ensure: vi.fn(),
  destroy: vi.fn(),
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: (doc: unknown) => {
    // Two queries run on this surface: the box list drives the table, the
    // env-spec list fills the dialog's picker.
    const body = String((doc as { loc?: { source?: { body?: string } } })?.loc?.source?.body ?? "");
    if (body.includes("agentEnvironmentSpecs")) {
      return {
        data: {
          agentEnvironmentSpecs: [
            {
              id: "s1",
              slug: "claude-dev",
              name: "Claude Dev",
              runtime: "claude",
              agentType: "claude",
            },
          ],
        },
        previousData: undefined,
        loading: false,
        error: undefined,
        refetch: vi.fn().mockResolvedValue({}),
      };
    }
    return {
      data: { agentBoxes: state.boxes },
      previousData: undefined,
      loading: false,
      error: undefined,
      refetch: vi.fn().mockResolvedValue({}),
    };
  },
  useMutation: (doc: unknown) => {
    const body = String((doc as { loc?: { source?: { body?: string } } })?.loc?.source?.body ?? "");
    return [body.includes("destroyAgentBox") ? state.destroy : state.ensure, { loading: false }];
  },
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// The list keeps its state in the URL; there is no app router under test.
vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

function box(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: "b1",
    name: "Claude Dev box",
    slug: "box-claude-dev-abcd1234",
    status: "running",
    agentSlug: "",
    environmentSpecSlug: "claude-dev",
    image: "agent-claude:1",
    idleTimeoutSeconds: 3600,
    sessionName: "astrolift",
    attachCommand: ["tmux", "new-session", "-A", "-s", "astrolift"],
    namespace: "astrolift-agents-acme",
    ownerEmail: "dev@example.com",
    lastError: "",
    startedAt: "2026-08-18T00:00:00Z",
    lastAttachedAt: null,
    createdAt: "2026-08-18T00:00:00Z",
    ...over,
  };
}

describe("BoxesTab", () => {
  beforeEach(() => {
    state.boxes = [];
    state.ensure = vi.fn().mockResolvedValue({
      data: { ensureAgentBox: { ok: true, errors: [], data: { slug: "box-claude-dev-abcd1234" } } },
    });
    state.destroy = vi.fn().mockResolvedValue({
      data: {
        destroyAgentBox: { ok: true, errors: [], data: { slug: "box-claude-dev-abcd1234" } },
      },
    });
  });

  it("an org with no boxes is told what one is for", () => {
    render(<BoxesTab orgId="org-1" />);

    expect(screen.getByText("No agent boxes")).toBeInTheDocument();
  });

  it("the button ensures a box with the chosen idle timeout", async () => {
    render(<BoxesTab orgId="org-1" />);

    fireEvent.click(screen.getByRole("button", { name: /New agent box/i }));
    fireEvent.click(screen.getByLabelText("Environment spec"));
    fireEvent.click(await screen.findByText(/Claude Dev \(claude\)/));
    fireEvent.click(screen.getByRole("button", { name: /Start box/i }));

    await waitFor(() => expect(state.ensure).toHaveBeenCalled());
    expect(state.ensure.mock.calls[0][0].variables).toMatchObject({
      orgId: "org-1",
      input: { environmentSpecSlug: "claude-dev", idleTimeoutSeconds: 3600 },
    });
  });

  it("a live box shows the command that attaches to it", () => {
    state.boxes = [box()];

    render(<BoxesTab orgId="org-1" />);

    expect(
      screen.getByText(
        "astro exec --app box-claude-dev-abcd1234 -- tmux new-session -A -s astrolift"
      )
    ).toBeInTheDocument();
  });

  it("a settled box offers neither an attach command nor a destroy button", () => {
    // Nothing to attach to and nothing left to kill — offering either would
    // be an action that silently does nothing.
    state.boxes = [box({ status: "expired" })];

    render(<BoxesTab orgId="org-1" />);

    expect(screen.queryByText(/astro exec --app/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Destroy/i })).not.toBeInTheDocument();
  });

  it("destroy targets the box that was clicked", async () => {
    state.boxes = [box(), box({ id: "b2", slug: "box-other-9999" })];

    render(<BoxesTab orgId="org-1" />);
    fireEvent.click(screen.getAllByRole("button", { name: /Destroy/i })[1]);

    await waitFor(() => expect(state.destroy).toHaveBeenCalled());
    expect(state.destroy.mock.calls[0][0].variables).toEqual({ slug: "box-other-9999" });
  });

  it("a never-reap box says so rather than showing 0", () => {
    state.boxes = [box({ idleTimeoutSeconds: 0 })];

    render(<BoxesTab orgId="org-1" />);

    expect(screen.getByText("Never")).toBeInTheDocument();
  });
});
