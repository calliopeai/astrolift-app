import { fireEvent, screen, waitFor } from "@testing-library/react";

import { renderWithIntl as render } from "@/test/render-with-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModulesCard } from "./modules-card";

/**
 * Modules flips a per-org switch through setOrganizationModule (#1880).
 *
 * The claims worth pinning: only the modules the server reports available
 * are listed at all (#2551), so a new install does not advertise the Chat
 * Studio integration and an install that offers it keeps the control; a
 * viewer without org.update gets read-only switches rather than a hidden
 * section (the issue's explicit "visible but read-only" contract); and a
 * click sends exactly the flipped {key, enabled} pair — not a toggle of
 * some other module and not a stale value.
 */

const state = vi.hoisted(() => ({
  modules: [] as Record<string, unknown>[],
  permissions: [] as string[],
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
    return { data: undefined, loading: false, error: undefined };
  },
  useMutation: () => [state.setModule, { loading: false }],
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

function moduleEntitlement(
  key: string,
  enabled: boolean,
  install: { available?: boolean; licensed?: boolean; source?: string | null } = {}
) {
  return {
    key,
    canView: true,
    canCreate: true,
    canManage: true,
    canRun: true,
    enabled,
    licensed: install.licensed ?? true,
    available: install.available ?? true,
    source: install.source ?? null,
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

  it("does not list the Chat Studio integration on an install that does not offer it (#2551)", () => {
    state.modules[0] = moduleEntitlement("chat_studio_integration", false, { available: false });
    render(<ModulesCard />);

    expect(screen.queryByText("Chat Studio integration")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: /chat studio integration/i })).toBeNull();
    // The modules the install does offer stay listed and switchable.
    expect(screen.getByRole("switch", { name: /enable agent live attach/i })).toBeEnabled();
    expect(screen.getByRole("switch", { name: /enable chat studio agent runs/i })).toBeEnabled();
  });

  it("keeps the Chat Studio integration's control where the install offers it", async () => {
    state.modules[0] = moduleEntitlement("chat_studio_integration", false);
    render(<ModulesCard />);

    const toggle = screen.getByRole("switch", { name: /enable chat studio integration/i });
    expect(toggle).toBeEnabled();
    expect(
      screen.getByText("Lets Chat Studio ship apps to this organization through the builder API.")
    ).toBeInTheDocument();

    fireEvent.click(toggle);
    await waitFor(() => expect(state.setModule).toHaveBeenCalled());
    expect(state.setModule.mock.calls[0][0].variables).toEqual({
      input: { key: "chat_studio_integration", enabled: true },
    });
  });

  it("does not list a module the licence withholds (#1879)", () => {
    state.modules[2] = moduleEntitlement("chat_studio_agent_runs", false, {
      available: false,
      licensed: false,
      source: "licence",
    });
    render(<ModulesCard />);

    expect(screen.queryByRole("switch", { name: /chat studio agent runs/i })).toBeNull();
    expect(screen.getByRole("switch", { name: /disable chat studio integration/i })).toBeEnabled();
  });

  it("says so when the install offers no module", () => {
    state.modules = state.modules.map((m) => ({ ...m, enabled: false, available: false }));
    render(<ModulesCard />);

    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByText("No modules are available on this install.")).toBeInTheDocument();
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
