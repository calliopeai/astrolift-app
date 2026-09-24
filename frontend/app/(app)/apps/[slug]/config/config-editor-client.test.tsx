import type { ReactNode } from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigEditorClient } from "./config-editor-client";

/**
 * The Apply confirm dialog names the env keys the staged draft changes
 * (#1759). The server computes that list with the same diff
 * applyStagedManifest gates on, across [env] and every container, job and
 * task env table. The dialog used to parse [env] itself, line by line, and
 * so said nothing when the change sat on a container.
 */

const BEFORE = `name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

[[workloads.containers]]
name = "web"
is_primary = true
`;

// Container env only: the top-level [env] table is untouched.
const STAGED = BEFORE.replace(
  "is_primary = true",
  'is_primary = true\nenv = { DATABASE_URL = "postgres://evil" }'
);

const state = vi.hoisted(() => ({ stagedEnvChanges: [] as string[] }));

function app() {
  return {
    id: "app-1",
    slug: "hello",
    name: "Hello",
    manifestPath: "astrolift.toml",
    deployBranch: "main",
    manifestSyncState: "db_ahead",
    updatedAt: "2026-09-24T00:00:00Z",
    sourceRepo: "",
    sourceUrl: "",
    rawManifest: BEFORE,
    rawManifestStaged: STAGED,
    rawManifestStagedHash: "digest",
    stagedEnvChanges: state.stagedEnvChanges,
  };
}

vi.mock("@apollo/client/react", () => ({
  useQuery: (doc: { definitions?: { kind: string; name?: { value: string } }[] }) => {
    const op = doc.definitions?.find((d) => d.kind === "OperationDefinition")?.name?.value ?? "";
    return {
      data: op === "GetApp" ? { astroliftApp: app() } : undefined,
      loading: false,
    };
  },
  useMutation: () => [vi.fn(), { loading: false }],
}));

// Keys verbatim, with any interpolation values appended, so assertions can
// read which message was chosen and what went into it.
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, unknown>) =>
    values ? `${key} ${JSON.stringify(values)}` : key,
}));

vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({ formatRelativeTime: () => "just now" }),
}));

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ actions, children }: { actions?: ReactNode; children?: ReactNode }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));

vi.mock("@/components/ConfirmDialog", () => ({
  ConfirmDialog: ({
    open,
    title,
    description,
  }: {
    open: boolean;
    title: ReactNode;
    description?: ReactNode;
  }) =>
    open ? (
      <div role="dialog" aria-label={String(title)}>
        {description}
      </div>
    ) : null,
}));

vi.mock("../components/app-tabs", () => ({ AppTabs: () => null }));
vi.mock("./manifest-form-pane", () => ({ ManifestFormPane: () => null }));
vi.mock("./agent-config-form-pane", () => ({ AgentConfigFormPane: () => null }));

beforeEach(() => {
  state.stagedEnvChanges = [];
});

function openApplyDialog() {
  render(<ConfigEditorClient slug="hello" />);
  fireEvent.click(screen.getByRole("button", { name: "applyStaged" }));
  return screen.getByRole("dialog", { name: "confirmApply.title" });
}

describe("ConfigEditorClient apply confirmation", () => {
  it("names the env keys the server reports, container env included", () => {
    state.stagedEnvChanges = ["workloads.web.containers.web.env.DATABASE_URL"];

    const dialog = openApplyDialog();

    expect(dialog).toHaveTextContent("confirmApply.descriptionWithEnv");
    expect(dialog).toHaveTextContent("workloads.web.containers.web.env.DATABASE_URL");
    expect(dialog).not.toHaveTextContent("postgres://evil");
  });

  it("uses the plain description when the server reports no env change", () => {
    const dialog = openApplyDialog();

    expect(dialog).toHaveTextContent("confirmApply.description");
    expect(dialog).not.toHaveTextContent("descriptionWithEnv");
  });
});
