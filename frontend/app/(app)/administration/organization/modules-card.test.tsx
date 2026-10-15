import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithIntl as render } from "@/test/render-with-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModulesCard } from "./modules-card";

/**
 * Modules flips a per-org switch through setOrganizationModule (#1880).
 *
 * The claims worth pinning: the install-wide kill switch disables the
 * toggle and says why regardless of what this org's own row says, a
 * viewer without org.update gets read-only switches rather than a hidden
 * section (the issue's explicit "visible but read-only" contract), and a
 * click sends exactly the flipped {key, enabled} pair — not a toggle of
 * some other module and not a stale value.
 */

const state = vi.hoisted(() => ({
  modules: [] as Record<string, unknown>[],
  permissions: [] as string[],
  featureFlags: [] as Record<string, unknown>[],
  setModule: vi.fn(),
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: (doc: { definitions?: { kind: string; name?: { value: string } }[] }) => {
    const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
    if (op === "Me") {
      return {
        data: { me: { id: "u1", profile: null, modules: state.modules } },
        loading: false,
        error: undefined,
      };
    }
    if (op === "GetMyPermissions") {
      return {
        data: { astroliftMyPermissions: state.permissions },
        loading: false,
        error: undefined,
      };
    }
    if (op === "AstroliftServerInfo") {
      return {
        data: { astroliftServerInfo: { featureFlags: state.featureFlags } },
        loading: false,
        error: undefined,
      };
    }
    return { data: undefined, loading: false, error: undefined };
  },
  useMutation: () => [state.setModule, { loading: false }],
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

function moduleEntitlement(
  key: string,
  enabled: boolean,
  licence: { licensed?: boolean; source?: string | null } = {}
) {
  return {
    key,
    canView: true,
    canCreate: true,
    canManage: true,
    canRun: true,
    enabled,
    licensed: licence.licensed ?? true,
    source: licence.source ?? null,
  };
}

describe("ModulesCard", () => {
  beforeEach(() => {
    state.modules = [
      moduleEntitlement("chat_studio_integration", true),
      moduleEntitlement("agent_live_attach", false),
      moduleEntitlement("chat_studio_agent_runs", false),
      moduleEntitlement("agent_policy_enforcement", false),
    ];
    state.permissions = ["org.update"];
    state.featureFlags = [
      { key: "modules.chat_studio_integration_allowed", enabled: true },
      { key: "modules.agent_live_attach_allowed", enabled: false },
      { key: "modules.chat_studio_agent_runs_allowed", enabled: true },
      { key: "modules.agent_policy_enforcement_allowed", enabled: true },
    ];
    state.setModule = vi.fn().mockResolvedValue({
      data: {
        setOrganizationModule: {
          ok: true,
          errors: [],
          data: { key: "chat_studio_integration", enabled: false },
        },
      },
    });
  });

  it("renders each module's current enabled state", () => {
    render(<ModulesCard />);

    expect(
      screen.getByRole("switch", { name: /disable chat studio integration/i })
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: /enable agent live attach/i })).toHaveAttribute(
      "aria-checked",
      "false"
    );
    expect(screen.getByRole("switch", { name: /enable chat studio agent runs/i })).toHaveAttribute(
      "aria-checked",
      "false"
    );
  });

  it("disables a module the install has forced off and says why", () => {
    render(<ModulesCard />);

    expect(screen.getByRole("switch", { name: /enable agent live attach/i })).toBeDisabled();
    expect(screen.getByText("Turned off on this install")).toBeInTheDocument();

    // The install-allowed module stays interactive for an org.update viewer.
    expect(screen.getByRole("switch", { name: /disable chat studio integration/i })).toBeEnabled();
  });

  it("is read-only for a viewer without org.update", () => {
    state.permissions = [];
    render(<ModulesCard />);

    expect(screen.getByRole("switch", { name: /disable chat studio integration/i })).toBeDisabled();
    expect(screen.getByRole("switch", { name: /enable agent live attach/i })).toBeDisabled();
  });

  it("sends the flipped {key, enabled} pair for the toggled module only", async () => {
    render(<ModulesCard />);

    fireEvent.click(screen.getByRole("switch", { name: /disable chat studio integration/i }));

    await waitFor(() => expect(state.setModule).toHaveBeenCalled());
    expect(state.setModule.mock.calls[0][0].variables).toEqual({
      input: { key: "chat_studio_integration", enabled: false },
    });
  });

  it("disables a module the licence withholds and says why (#1879)", () => {
    state.modules[2] = moduleEntitlement("chat_studio_agent_runs", false, {
      licensed: false,
      source: "licence",
    });
    render(<ModulesCard />);

    expect(screen.getByRole("switch", { name: /enable chat studio agent runs/i })).toBeDisabled();
    expect(screen.getByText("Not included in this install's license")).toBeInTheDocument();
  });

  it("says when the licence turned a module off and leaves it for the org admin", () => {
    state.modules[2] = moduleEntitlement("chat_studio_agent_runs", false, { source: "licence" });
    render(<ModulesCard />);

    expect(screen.getByRole("switch", { name: /enable chat studio agent runs/i })).toBeEnabled();
    expect(
      screen.getByText("Turned off when the license withheld it. An org admin can turn it back on.")
    ).toBeInTheDocument();
  });

  it("translates a licence refusal by its code, not the server's English", async () => {
    state.setModule = vi.fn().mockResolvedValue({
      data: {
        setOrganizationModule: {
          ok: false,
          errors: [{ code: "MODULE_NOT_LICENSED", message: "server english" }],
          data: null,
        },
      },
    });
    render(<ModulesCard />);

    fireEvent.click(screen.getByRole("switch", { name: /enable chat studio agent runs/i }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "This module is not included in this install's license."
      )
    );
  });
});
