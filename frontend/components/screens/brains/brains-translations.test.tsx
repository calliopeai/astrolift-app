import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { locales } from "@/i18n/config";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { brainMcpConfig } from "./brain-connection";
import type { BrainSummary } from "./brain-status-model";
import { BEHIND, DEPLOY_FAILED, HEALTHY, summaryOf, UNKNOWN_VALUES } from "./brain-status.fixtures";
import { ARCHIVED_BRAIN, BRAINS, CONNECTION, HOST_PROJECTS } from "./brains.fixtures";
import { BRAINS_LIST } from "./brains-list";
import { BrainsScreen } from "./BrainsScreen";
import type { HostBrainAppValues } from "./HostBrainAppDialog";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

function Screen({
  rows = BRAINS,
  summaries,
  connection = null,
  onConnect = async () => {},
  hostingSlug = null,
  onHost = () => {},
  onSubmitHost = async () => {},
  canCreate = true,
}: {
  rows?: typeof BRAINS;
  /** Each row's cached summary by slug, over the fixture's. */
  summaries?: Record<string, BrainSummary | null>;
  connection?: typeof CONNECTION | null;
  onConnect?: (slug: string) => Promise<void>;
  hostingSlug?: string | null;
  onHost?: (slug: string) => void;
  onSubmitHost?: (values: HostBrainAppValues) => Promise<void>;
  canCreate?: boolean;
}) {
  const list = useLocalListState(BRAINS_LIST);
  return (
    <BrainsScreen
      list={list}
      rows={rows.map((r) => (summaries ? { ...r, summary: summaries[r.slug] ?? null } : r))}
      totalCount={rows.length}
      nextCursor={null}
      loading={false}
      stale={false}
      error={null}
      onRetry={() => {}}
      canCreate={canCreate}
      connection={connection}
      connecting={false}
      onConnect={onConnect}
      onDismissConnection={() => {}}
      hostingSlug={hostingSlug}
      hostProjects={HOST_PROJECTS}
      hostSubmitting={false}
      onHost={onHost}
      onDismissHost={() => {}}
      onSubmitHost={onSubmitHost}
    />
  );
}

function inEnglish(ui: React.ReactElement) {
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      {ui}
    </NextIntlClientProvider>
  );
}

describe("semantic layers list (#2320, #2427)", () => {
  it.each(locales)("%s renders every key without falling back to English", (locale) => {
    const onError = vi.fn();
    const copy = catalogs[locale].brains;
    expect(Object.keys(copy).sort()).toEqual(Object.keys(en.brains).sort());
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={onError}
      >
        <Screen
          rows={[...BRAINS, ARCHIVED_BRAIN]}
          connection={CONNECTION}
          summaries={{
            "leo-brain": summaryOf(BEHIND),
            "sales-brain": summaryOf(HEALTHY, false),
            "old-brain": summaryOf(HEALTHY),
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(copy.connectDialog.config)).toBeInTheDocument();
    expect(screen.getAllByText(copy.title).length).toBeGreaterThan(0);
    expect(screen.getByText(copy.columnHealth)).toBeInTheDocument();
    expect(screen.getByText(copy.columnFreshness)).toBeInTheDocument();
    expect(screen.getByText(copy.status.behind)).toBeInTheDocument();
    expect(screen.getByTitle(copy.status.notChecked)).toHaveTextContent(copy.status.unknown);
    expect(screen.getByText(copy.archived)).toBeInTheDocument();
    expect(screen.getAllByText(copy.notShared).length).toBeGreaterThan(0);
    if (locale !== "en") expect(copy.context).not.toBe(en.brains.context);
    expect(onError).not.toHaveBeenCalled();
  });

  it("opens each layer's own page, and links its shares and the create flow", () => {
    inEnglish(<Screen />);
    const row = screen.getByRole("row", { name: /Leo's brain/ });
    expect(within(row).getAllByRole("link")[0]).toHaveAttribute("href", "/brains/leo-brain");
    expect(screen.getByText("medops")).toHaveAttribute("title", "medops: viewer");
    expect(screen.getByRole("link", { name: en.brains.new })).toHaveAttribute(
      "href",
      "/brains/new"
    );
  });

  it("names the row link by name and slug, apart", () => {
    inEnglish(<Screen />);
    expect(screen.getByRole("link", { name: "Leo's brain leo-brain" })).toBeInTheDocument();
  });

  it("shows each row's health, last rebuild and freshness from its cached summary", () => {
    inEnglish(
      <Screen
        summaries={{
          "leo-brain": summaryOf(DEPLOY_FAILED),
          "sales-brain": summaryOf(UNKNOWN_VALUES),
        }}
      />
    );
    const failed = within(screen.getByRole("row", { name: /Leo's brain/ }));
    expect(failed.getByText(en.apps.deployments.statuses.failed)).toBeInTheDocument();
    expect(failed.getByText("b7e1d03")).toBeInTheDocument();
    // Nothing runs after a failed first deploy, so there is no compile to read.
    expect(failed.getByText(en.brains.overview.nothingRunning)).toBeInTheDocument();
    // A status this build does not know reads as itself, never as a key.
    const unknown = within(screen.getByRole("row", { name: /Sales brain/ }));
    expect(unknown.getByText("paused_by_operator")).toBeInTheDocument();
  });

  it("says a row's environment does not exist rather than guessing its health", () => {
    inEnglish(<Screen summaries={{}} />);
    expect(screen.getAllByText("No production environment")).toHaveLength(2);
  });

  it("lists an archived layer with a badge, its page a click away, and nothing to connect", () => {
    inEnglish(<Screen rows={[ARCHIVED_BRAIN]} />);
    const row = within(screen.getByRole("row", { name: /Old brain/ }));
    expect(row.getByText(en.brains.archived)).toBeInTheDocument();
    expect(row.getAllByRole("link")[0]).toHaveAttribute("href", "/brains/old-brain");
    expect(row.getByRole("button", { name: en.brains.connect })).toBeDisabled();
    expect(row.getByRole("button", { name: en.brains.host })).toBeDisabled();
  });

  it("connects a served layer and not one that is not served", () => {
    const onConnect = vi.fn(async () => {});
    inEnglish(<Screen onConnect={onConnect} />);
    const served = within(screen.getByRole("row", { name: /Leo's brain/ }));
    const notServed = within(screen.getByRole("row", { name: /Sales brain/ }));
    expect(notServed.getByRole("button", { name: en.brains.connect })).toBeDisabled();
    fireEvent.click(served.getByRole("button", { name: en.brains.connect }));
    expect(onConnect).toHaveBeenCalledWith("leo-brain");
  });

  it("shows the issued token once as an MCP client config", () => {
    inEnglish(<Screen connection={CONNECTION} />);
    const dialog = within(screen.getByRole("alertdialog"));
    expect(dialog.getByText("https://brain.acme.example/mcp")).toBeInTheDocument();
    expect(JSON.parse(brainMcpConfig(CONNECTION))).toEqual({
      mcpServers: {
        "leo-brain": {
          type: "http",
          url: "https://brain.acme.example/mcp",
          headers: { "X-Astrolift-Brain-Token": CONNECTION.token },
        },
      },
    });
    expect(
      dialog.getByText((_, el) => el?.textContent?.startsWith('{\n  "mcpServers"') ?? false, {
        selector: "div",
      })
    ).toHaveTextContent(CONNECTION.token);
  });

  it.each(locales)("%s renders the host dialog without falling back to English", (locale) => {
    const onError = vi.fn();
    const copy = catalogs[locale].brains;
    render(
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={onError}
      >
        <Screen hostingSlug="leo-brain" />
      </NextIntlClientProvider>
    );
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText(copy.hostDialog.envHelp)).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: copy.hostDialog.create })).toBeDisabled();
    if (locale !== "en")
      expect(copy.hostDialog.description).not.toBe(en.brains.hostDialog.description);
    expect(onError).not.toHaveBeenCalled();
  });

  it("hosts an app on a served layer from the template", () => {
    const onHost = vi.fn();
    const onSubmitHost = vi.fn(async () => {});
    const { rerender } = inEnglish(<Screen onHost={onHost} />);
    const served = within(screen.getByRole("row", { name: /Leo's brain/ }));
    const notServed = within(screen.getByRole("row", { name: /Sales brain/ }));
    expect(notServed.getByRole("button", { name: en.brains.host })).toBeDisabled();
    fireEvent.click(served.getByRole("button", { name: en.brains.host }));
    expect(onHost).toHaveBeenCalledWith("leo-brain");

    rerender(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Screen hostingSlug="leo-brain" onSubmitHost={onSubmitHost} />
      </NextIntlClientProvider>
    );
    const dialog = within(screen.getByRole("dialog"));
    fireEvent.change(dialog.getByLabelText(en.brains.hostDialog.image), {
      target: { value: " ghcr.io/acme/brain-chat:1.0 " },
    });
    fireEvent.change(dialog.getByLabelText(en.brains.hostDialog.port), {
      target: { value: "3000" },
    });
    fireEvent.click(dialog.getByRole("button", { name: en.brains.hostDialog.create }));
    expect(onSubmitHost).toHaveBeenCalledWith({
      projectId: HOST_PROJECTS[0].id,
      image: "ghcr.io/acme/brain-chat:1.0",
      name: "",
      port: 3000,
      expiresIn: "",
      deleteDataOnExpiry: false,
    });
  });

  it("offers neither create nor host without app.create; Connect stays", () => {
    inEnglish(<Screen canCreate={false} />);
    expect(screen.queryByRole("link", { name: en.brains.new })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.brains.host })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: en.brains.connect })).toHaveLength(2);
  });

  it("shows the empty state, with create when the viewer may", () => {
    inEnglish(<Screen rows={[]} />);
    expect(screen.getByText(en.brains.emptyTitle)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: en.brains.new }).length).toBeGreaterThan(0);
  });
});
