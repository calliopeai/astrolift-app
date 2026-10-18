import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type * as React from "react";
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

import { CONNECTION, HOST_PROJECTS } from "../brains.fixtures";
import type { BrainStatus } from "../brain-status-model";
import {
  BEHIND,
  DEPLOY_FAILED,
  GRADUATION_NEEDED,
  HEALTHY,
  NOT_ADOPTED,
  ON_POSTGRES,
  UNKNOWN_VALUES,
} from "../brain-status.fixtures";
import type { BrainStatusResult } from "../brain-status-model";
import { CELLS } from "../brain-cells.fixtures";
import { draftFromSubstrate } from "../substrate-cells";
import { BRAIN_APPS_LIST, BRAIN_SHARES_LIST } from "./brain-detail-lists";
import { DEFAULT_ASK, EVERYTHING, hostedAppRows, NOTHING } from "./brain-detail-model";
import { BRAIN, HISTORY, NOT_SERVED, TEAM_RESULTS } from "./brain-detail.fixtures";
import { BrainAppsTab } from "./BrainAppsTab";
import { BrainConnectTab } from "./BrainConnectTab";
import { BrainFrame, type BrainFrameProps } from "./BrainFrame";
import { BrainOverviewTab } from "./BrainOverviewTab";
import { BrainSettingsTab, type BrainSettingsTabProps } from "./BrainSettingsTab";
import { BrainSharingTab, type BrainSharingTabProps } from "./BrainSharingTab";
import { BrainSubstrateTab, type BrainSubstrateTabProps } from "./BrainSubstrateTab";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
type Locale = keyof typeof catalogs;

function Intl({
  locale = "en",
  onError,
  children,
}: {
  locale?: Locale;
  onError?: () => void;
  children: React.ReactNode;
}) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      onError={onError}
    >
      {children}
    </NextIntlClientProvider>
  );
}

const ready = (status: BrainStatus | null): BrainStatusResult => ({ state: "ready", status });

const FRAME: Omit<BrainFrameProps, "children"> = {
  slug: BRAIN.slug,
  pathname: `/brains/${BRAIN.slug}`,
  brain: BRAIN,
  loading: false,
  error: null,
  onRetry: () => {},
  archived: null,
  restoring: false,
  onRestore: () => {},
  can: EVERYTHING,
  connecting: false,
  onConnect: () => {},
  connection: null,
  onDismissConnection: () => {},
};

const SECTIONS = {
  sharing: [
    { id: "teams", label: "Teams", href: "/brains/platform-brain/sharing" },
    {
      id: "history",
      label: "Access history",
      href: "/brains/platform-brain/sharing?section=history",
    },
  ],
  settings: [
    { id: "rebuild", label: "Rebuild", href: "/brains/platform-brain/settings" },
    {
      id: "danger-zone",
      label: "Danger zone",
      href: "/brains/platform-brain/settings?section=danger-zone",
    },
  ],
};

function Sharing(patch: Partial<BrainSharingTabProps>) {
  const list = useLocalListState(BRAIN_SHARES_LIST);
  return (
    <BrainSharingTab
      layerName={BRAIN.name}
      homeTeam={BRAIN.team}
      teamsCardHref="/apps/platform-brain/settings"
      section="teams"
      sections={SECTIONS.sharing}
      list={list}
      rows={BRAIN.sharedWith}
      totalCount={BRAIN.sharedWith.length}
      canShare
      canShareAsEditor
      ask={DEFAULT_ASK}
      share={{
        team: null,
        onTeam: () => {},
        search: { query: "", setQuery: () => {}, results: TEAM_RESULTS },
        level: "viewer",
        onLevel: () => {},
        submitting: false,
        onShare: async () => true,
      }}
      onChangeLevel={async () => true}
      onUnshare={async () => true}
      refusal={null}
      history={{
        allowed: true,
        items: HISTORY,
        loading: false,
        error: null,
        onRetry: () => {},
        hasMore: false,
        loadingMore: false,
        onLoadMore: () => {},
      }}
      {...patch}
    />
  );
}

function Apps({ canHost = true, hosting = false }: { canHost?: boolean; hosting?: boolean }) {
  const list = useLocalListState(BRAIN_APPS_LIST);
  const rows = hostedAppRows(BRAIN.hostedApps ?? []);
  return (
    <BrainAppsTab
      slug={BRAIN.slug}
      served
      canHost={canHost}
      list={list}
      rows={rows}
      totalCount={rows.length}
      hosting={hosting}
      onHost={() => {}}
      onDismissHost={() => {}}
      hostProjects={HOST_PROJECTS}
      hostSubmitting={false}
      onSubmitHost={() => {}}
    />
  );
}

const GRADUATION: BrainSubstrateTabProps["graduation"] = {
  open: false,
  onOpen: () => {},
  onClose: () => {},
  draft: draftFromSubstrate(HEALTHY.substrate),
  onChange: () => {},
  locked: [],
  matrix: CELLS,
  submitting: false,
  onSubmit: () => {},
  error: null,
};

function Substrate(patch: Partial<BrainSubstrateTabProps>) {
  return (
    <BrainSubstrateTab
      local={BRAIN.substrate ?? null}
      status={ready(HEALTHY)}
      environment="production"
      environments={[]}
      onRetry={() => {}}
      graduateNeeds={null}
      graduation={GRADUATION}
      {...patch}
    />
  );
}

const SETTINGS: BrainSettingsTabProps = {
  layerName: BRAIN.name,
  section: "rebuild",
  sections: SECTIONS.settings,
  canRebuild: true,
  canArchive: true,
  rebuild: {
    environments: ["production", "staging"],
    environment: "production",
    onEnvironment: () => {},
    ingest: false,
    onIngest: () => {},
    submitting: false,
    onRebuild: () => {},
    result: {
      slug: BRAIN.slug,
      mode: "ingest",
      deploymentId: null,
      runUrl: "https://github.example.test/run/1",
      ingestRunUrl: "https://github.example.test/run/1",
      rebuilds: ["the projections the brain rebuilds at deploy"],
    },
    error: { message: "dispatch failed", approval: false },
  },
  archive: {
    ask: { ...DEFAULT_ASK, approval: true, minimumApprovals: 2 },
    submitting: false,
    onArchive: async () => true,
    error: null,
  },
};

const ENVIRONMENTS = [
  { name: "production", href: "?env=production", active: true },
  { name: "staging", href: "?env=staging", active: false },
];

/** Every screen of the detail, in the states that carry their own words. */
function everyScreen(): React.ReactElement[] {
  return [
    <BrainFrame key="f1" {...FRAME}>
      <p>body</p>
    </BrainFrame>,
    <BrainFrame key="f2" {...FRAME} brain={NOT_SERVED} connection={CONNECTION}>
      <p>body</p>
    </BrainFrame>,
    <BrainFrame
      key="f3"
      {...FRAME}
      brain={null}
      archived={{ name: BRAIN.name, archivedAt: "2026-10-16T08:00:00Z" }}
      can={NOTHING}
    >
      <p>body</p>
    </BrainFrame>,
    <BrainFrame key="f4" {...FRAME} brain={null} error={{ message: "denied", denied: true }}>
      <p>body</p>
    </BrainFrame>,
    <BrainFrame
      key="f5"
      {...FRAME}
      brain={null}
      error={{ message: "Network error", denied: false }}
    >
      <p>body</p>
    </BrainFrame>,
    <BrainFrame key="f6" {...FRAME} brain={null}>
      <p>body</p>
    </BrainFrame>,
    <BrainOverviewTab
      key="o1"
      brain={BRAIN}
      environment="production"
      environments={ENVIRONMENTS}
      status={ready(BEHIND)}
      onRetry={() => {}}
    />,
    <BrainOverviewTab
      key="o2"
      brain={BRAIN}
      environment="production"
      environments={ENVIRONMENTS}
      status={ready(DEPLOY_FAILED)}
      onRetry={() => {}}
    />,
    <BrainOverviewTab
      key="o3"
      brain={NOT_SERVED}
      environment="production"
      environments={[]}
      status={ready(null)}
      onRetry={() => {}}
    />,
    <BrainOverviewTab
      key="o4"
      brain={BRAIN}
      environment="production"
      environments={[]}
      status={{ state: "error", message: "502" }}
      onRetry={() => {}}
    />,
    <BrainOverviewTab
      key="o5"
      brain={BRAIN}
      environment="production"
      environments={[]}
      status={ready(UNKNOWN_VALUES)}
      onRetry={() => {}}
    />,
    <Substrate key="s1" status={ready(GRADUATION_NEEDED)} />,
    <Substrate key="s2" status={ready(GRADUATION_NEEDED)} graduateNeeds="app.deploy" />,
    <Substrate key="s3" status={ready(NOT_ADOPTED)} />,
    <Substrate
      key="s4"
      status={ready(ON_POSTGRES)}
      graduation={{ ...GRADUATION, open: true, error: { field: "persistence", message: "no" } }}
    />,
    <Substrate
      key="s5"
      local={BRAIN.substrate ?? null}
      status={{ state: "error", message: "502" }}
    />,
    <Sharing key="h1" />,
    <Sharing
      key="h2"
      canShare={false}
      canShareAsEditor={false}
      refusal={{ message: "no", approval: true }}
      ask={{ ...DEFAULT_ASK, approval: true, minimumApprovals: 3 }}
    />,
    <Sharing key="h3" section="history" />,
    <Sharing
      key="h4"
      section="history"
      history={{
        allowed: false,
        items: [],
        loading: false,
        error: null,
        onRetry: () => {},
        hasMore: false,
        loadingMore: false,
        onLoadMore: () => {},
      }}
    />,
    <Apps key="a1" hosting />,
    <Apps key="a2" canHost={false} />,
    <BrainConnectTab
      key="c1"
      slug={BRAIN.slug}
      endpoints={BRAIN.environments}
      connecting={false}
      onConnect={() => {}}
    />,
    <BrainConnectTab
      key="c2"
      slug={NOT_SERVED.slug}
      endpoints={[]}
      connecting={false}
      onConnect={() => {}}
    />,
    <BrainSettingsTab key="t1" {...SETTINGS} />,
    <BrainSettingsTab
      key="t2"
      {...SETTINGS}
      section="danger-zone"
      archive={{ ...SETTINGS.archive, error: { message: "approval", approval: true } }}
    />,
    <BrainSettingsTab key="t3" {...SETTINGS} canRebuild={false} canArchive={false} />,
  ];
}

describe("the semantic layer detail in every locale (#2427)", () => {
  it.each(locales)("%s renders the frame and every tab in its own words", (locale) => {
    const onError = vi.fn();
    for (const ui of everyScreen()) {
      const view = render(
        <Intl locale={locale as Locale} onError={onError}>
          {ui}
        </Intl>
      );
      view.unmount();
    }
    const copy = catalogs[locale as Locale].brains;
    render(
      <Intl locale={locale as Locale} onError={onError}>
        <BrainFrame {...FRAME}>
          <p>body</p>
        </BrainFrame>
      </Intl>
    );
    expect(screen.getByRole("link", { name: copy.tabs.substrate })).toHaveAttribute(
      "href",
      "/brains/platform-brain/substrate"
    );
    expect(onError).not.toHaveBeenCalled();
    if (locale !== "en")
      expect(copy.frame.archivedDescription).not.toBe(en.brains.frame.archivedDescription);
  });
});

describe("the frame", () => {
  it("connects from the header only when served", () => {
    const onConnect = vi.fn();
    const { rerender } = render(
      <Intl>
        <BrainFrame {...FRAME} onConnect={onConnect}>
          <p>body</p>
        </BrainFrame>
      </Intl>
    );
    fireEvent.click(screen.getByRole("button", { name: en.brains.frame.connect }));
    expect(onConnect).toHaveBeenCalledTimes(1);
    rerender(
      <Intl>
        <BrainFrame {...FRAME} brain={NOT_SERVED} onConnect={onConnect}>
          <p>body</p>
        </BrainFrame>
      </Intl>
    );
    expect(screen.getByRole("button", { name: en.brains.frame.connect })).toBeDisabled();
  });

  it("renders the tab body only once the layer is found", () => {
    render(
      <Intl>
        <BrainFrame {...FRAME} brain={null}>
          <p>body</p>
        </BrainFrame>
      </Intl>
    );
    expect(screen.queryByText("body")).not.toBeInTheDocument();
    expect(screen.getByText("No semantic layer named platform-brain")).toBeInTheDocument();
  });

  it("restores an archived layer for a viewer who may, and says what it takes otherwise", () => {
    const onRestore = vi.fn();
    const archived = { name: BRAIN.name, archivedAt: null };
    const { rerender } = render(
      <Intl>
        <BrainFrame {...FRAME} brain={null} archived={archived} onRestore={onRestore}>
          <p>body</p>
        </BrainFrame>
      </Intl>
    );
    fireEvent.click(screen.getByRole("button", { name: en.brains.frame.restore }));
    expect(onRestore).toHaveBeenCalled();
    rerender(
      <Intl>
        <BrainFrame {...FRAME} brain={null} archived={archived} can={NOTHING}>
          <p>body</p>
        </BrainFrame>
      </Intl>
    );
    expect(screen.queryByRole("button", { name: en.brains.frame.restore })).not.toBeInTheDocument();
    expect(screen.getByText("app.update")).toBeInTheDocument();
  });
});

describe("the tabs", () => {
  it("Connect issues nothing until its button is pressed", () => {
    const onConnect = vi.fn();
    render(
      <Intl>
        <BrainConnectTab
          slug={BRAIN.slug}
          endpoints={BRAIN.environments}
          connecting={false}
          onConnect={onConnect}
        />
      </Intl>
    );
    expect(onConnect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: en.brains.connectTab.connect }));
    expect(onConnect).toHaveBeenCalledTimes(1);
  });

  it("Substrate offers graduation only when Mimwell asks for it", () => {
    const { rerender } = render(
      <Intl>
        <Substrate status={ready(HEALTHY)} />
      </Intl>
    );
    expect(
      screen.queryByRole("button", { name: en.brains.substrateTab.graduate })
    ).not.toBeInTheDocument();
    rerender(
      <Intl>
        <Substrate status={ready(GRADUATION_NEEDED)} />
      </Intl>
    );
    expect(
      screen.getByRole("button", { name: en.brains.substrateTab.graduate })
    ).toBeInTheDocument();
  });

  it("Sharing shares after confirmation, and only with a team picked", async () => {
    const onShare = vi.fn(async () => true);
    const { rerender } = render(
      <Intl>
        <Sharing
          share={{
            team: null,
            onTeam: () => {},
            search: { query: "", setQuery: () => {}, results: TEAM_RESULTS },
            level: "viewer",
            onLevel: () => {},
            submitting: false,
            onShare,
          }}
        />
      </Intl>
    );
    expect(screen.getByRole("button", { name: en.brains.sharing.share })).toBeDisabled();
    rerender(
      <Intl>
        <Sharing
          share={{
            team: TEAM_RESULTS[0]!,
            onTeam: () => {},
            search: { query: "", setQuery: () => {}, results: TEAM_RESULTS },
            level: "deployer",
            onLevel: () => {},
            submitting: false,
            onShare,
          }}
        />
      </Intl>
    );
    fireEvent.click(screen.getByRole("button", { name: en.brains.sharing.share }));
    const dialog = within(await screen.findByRole("alertdialog"));
    expect(
      dialog.getByText("Share Platform knowledge with finance as Editor?")
    ).toBeInTheDocument();
    expect(onShare).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole("button", { name: en.brains.sharing.share }));
    expect(onShare).toHaveBeenCalledTimes(1);
  });

  it("Sharing never offers the owning team", () => {
    render(
      <Intl>
        <Sharing
          share={{
            team: { kind: "team", id: "platform", name: "Platform" },
            onTeam: () => {},
            search: { query: "", setQuery: () => {}, results: [] },
            level: "viewer",
            onLevel: () => {},
            submitting: false,
            onShare: async () => true,
          }}
        />
      </Intl>
    );
    expect(screen.getByRole("button", { name: en.brains.sharing.share })).toBeDisabled();
    expect(screen.getByText(en.brains.sharing.homeTeamNote)).toBeInTheDocument();
  });

  it("Settings archives after confirmation and says an approval was asked for", async () => {
    const onArchive = vi.fn(async () => true);
    render(
      <Intl>
        <BrainSettingsTab
          {...SETTINGS}
          section="danger-zone"
          archive={{
            ...SETTINGS.archive,
            onArchive,
            error: { message: "needs approval", approval: true },
          }}
        />
      </Intl>
    );
    expect(screen.getByRole("link", { name: en.brains.settings.viewRequests })).toHaveAttribute(
      "href",
      "/administration/permissions/safety"
    );
    expect(screen.getByText(/needs approval from 2 approvers/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.brains.settings.archive }));
    const dialog = within(await screen.findByRole("alertdialog"));
    fireEvent.click(dialog.getByRole("button", { name: en.brains.settings.archive }));
    expect(onArchive).toHaveBeenCalledTimes(1);
  });

  it("Apps lists each hosted app once and opens it", () => {
    render(
      <Intl>
        <Apps />
      </Intl>
    );
    const row = within(screen.getByRole("row", { name: /brain-chat/ }));
    expect(row.getAllByRole("link")[0]).toHaveAttribute("href", "/apps/brain-chat");
    expect(row.getByText("staging")).toBeInTheDocument();
  });
});
