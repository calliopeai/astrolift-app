import type { ReactNode } from "react";

import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithIntl as render } from "@/test/render-with-intl";

import { UPDATE_MANIFEST } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";

import { ConfigEditorClient } from "./config-editor-client";

const MASKED_VALUE = "[ASTROLIFT_REDACTED_ENV_VALUE]";
const STORED_VIEW = `name = "demo"\n\n[env]\nAPI_KEY = "${MASKED_VALUE}"\n`;

// The "server": the app GET_APP returns, plus a way for the test to land a
// refetch while a save is still in flight, which is what
// awaitRefetchQueries does in the real client.
const server = vi.hoisted(() => {
  let app: Record<string, unknown> | null = null;
  const listeners = new Set<() => void>();
  return {
    get: () => app,
    set(next: Record<string, unknown>) {
      app = next;
      listeners.forEach((listener) => listener());
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    resolveSave: null as null | ((value: unknown) => void),
  };
});

vi.mock("@apollo/client/react", async () => {
  const React = await import("react");
  return {
    useQuery: (query: unknown) => {
      const app = React.useSyncExternalStore(server.subscribe, server.get);
      if (query === GET_APP) return { data: { astroliftApp: app }, loading: false };
      return { data: undefined, loading: false };
    },
    useMutation: (mutation: unknown) => {
      if (mutation !== UPDATE_MANIFEST) return [vi.fn(), { loading: false }];
      const save = () =>
        new Promise((resolve) => {
          server.resolveSave = resolve;
        });
      return [save, { loading: false }];
    },
  };
});

vi.mock("next-intl", async (original) => ({
  ...(await original<typeof import("next-intl")>()),
  useTranslations: () => (key: string) => key,
}));

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), message: vi.fn() }),
}));

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children, actions }: { children?: ReactNode; actions?: ReactNode }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));

vi.mock("@/components/ConfirmDialog", () => ({ ConfirmDialog: () => null }));
vi.mock("../components/app-tabs", () => ({ AppTabs: () => null }));
vi.mock("@/components/screens/apps/config/ManifestFormPane", () => ({
  ManifestFormPane: () => null,
}));
vi.mock("@/components/screens/apps/config/AgentConfigFormPane", () => ({
  AgentConfigFormPane: () => null,
}));

function appWithStaged(rawManifestStaged: string, updatedAt: string) {
  return {
    id: "app-1",
    slug: "demo",
    name: "Demo",
    rawManifest: "",
    rawManifestStaged,
    manifestSyncState: "db_ahead",
    manifestPath: "astrolift.toml",
    deployBranch: "main",
    sourceUrl: "",
    sourceRepo: "",
    updatedAt,
  };
}

beforeEach(() => {
  server.set(appWithStaged(STORED_VIEW, "2026-09-24T00:00:00Z"));
  server.resolveSave = null;
});

describe("ConfigEditorClient save round trip (#1920)", () => {
  it("keeps a conflicting local draft unsaved until the explicit Save action", async () => {
    render(<ConfigEditorClient slug="demo" />);
    fireEvent.click(screen.getByRole("button", { name: "view.code" }));
    const ours = `${STORED_VIEW}LOCAL = "retained"\n`;
    fireEvent.change(screen.getByPlaceholderText("# astrolift.toml"), { target: { value: ours } });
    act(() =>
      server.set(appWithStaged(`${STORED_VIEW}REMOTE = "changed"\n`, "2026-09-24T00:00:05Z"))
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("keepMine");
    expect(screen.queryByText("forceOverwrite")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "keepMine" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(server.resolveSave).toBeNull();
    expect(screen.getByPlaceholderText("# astrolift.toml")).toHaveValue(ours);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "saveDraft" })));
    expect(server.resolveSave).not.toBeNull();
    await act(async () =>
      server.resolveSave?.({
        data: { updateManifest: { ok: false, errors: [{ message: "Retry later" }] } },
      })
    );
    expect(screen.getByPlaceholderText("# astrolift.toml")).toHaveValue(ours);
  });

  it("adopts the masked echo of its own save instead of raising a conflict", async () => {
    render(<ConfigEditorClient slug="demo" />);
    fireEvent.click(screen.getByRole("button", { name: "view.code" }));

    // A viewer who can't reveal secrets types a brand-new value.
    const typed = `${STORED_VIEW}NEW_KEY = "typed-secret"\n`;
    const echoed = `${STORED_VIEW}NEW_KEY = "${MASKED_VALUE}"\n`;
    fireEvent.change(screen.getByPlaceholderText("# astrolift.toml"), { target: { value: typed } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "saveDraft" }));
    });
    // The refetch lands before updateManifest resolves, with the new value
    // masked the way the server masks it for this viewer.
    act(() => server.set(appWithStaged(echoed, "2026-09-24T00:00:05Z")));
    await act(async () => {
      server.resolveSave?.({
        data: {
          updateManifest: {
            ok: true,
            errors: [],
            data: {
              id: "app-1",
              syncState: "db_ahead",
              rawManifest: "",
              rawManifestStaged: echoed,
            },
          },
        },
      });
    });

    expect(screen.queryByText("conflict.banner")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("# astrolift.toml")).toHaveValue(echoed);
  });
});
