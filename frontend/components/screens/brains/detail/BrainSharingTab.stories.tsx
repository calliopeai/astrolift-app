import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, fn, userEvent, within } from "storybook/test";

import type { Principal } from "@/components/access/access-model";
import { selectRows } from "@/components/list/select-rows";
import { useLocalListState } from "@/components/list/use-list-state";

import { BRAIN_SHARES_LIST, BRAIN_SHARES_SELECT } from "./brain-detail-lists";
import { DEFAULT_ASK, type ShareLevel } from "./brain-detail-model";
import { BRAIN, HISTORY, TEAM_RESULTS } from "./brain-detail.fixtures";
import { BrainSharingTab, type BrainSharingTabProps } from "./BrainSharingTab";

const meta: Meta = {
  title: "Screens/Brains/Detail/BrainSharingTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const SECTIONS = [
  { id: "teams", label: "Teams", href: "/brains/platform-brain/sharing" },
  {
    id: "history",
    label: "Access history",
    href: "/brains/platform-brain/sharing?section=history",
  },
];

const HISTORY_PROPS: BrainSharingTabProps["history"] = {
  allowed: true,
  items: HISTORY,
  loading: false,
  error: null,
  onRetry: () => {},
  hasMore: false,
  loadingMore: false,
  onLoadMore: () => {},
};

const onShare = fn(async () => true);
const onUnshare = fn(async () => true);
const onChangeLevel = fn(async () => true);

function Sharing({
  shares = BRAIN.sharedWith,
  ...patch
}: Partial<BrainSharingTabProps> & { shares?: { team: string; access: string }[] }) {
  const list = useLocalListState(BRAIN_SHARES_LIST);
  const page = selectRows(
    shares,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    BRAIN_SHARES_SELECT
  );
  const [team, setTeam] = React.useState<Principal | null>(null);
  const [level, setLevel] = React.useState<ShareLevel>("viewer");
  const [query, setQuery] = React.useState("");
  return (
    <BrainSharingTab
      layerName={BRAIN.name}
      homeTeam={BRAIN.team}
      teamsCardHref="/apps/platform-brain/settings"
      section="teams"
      sections={SECTIONS}
      list={list}
      rows={page.rows}
      totalCount={page.totalCount}
      canShare
      canShareAsEditor
      ask={DEFAULT_ASK}
      share={{
        team,
        onTeam: setTeam,
        search: {
          query,
          setQuery,
          results: TEAM_RESULTS.filter((r) => r.id.includes(query.trim().toLowerCase())),
        },
        level,
        onLevel: setLevel,
        submitting: false,
        onShare,
      }}
      onChangeLevel={onChangeLevel}
      onUnshare={onUnshare}
      refusal={null}
      history={HISTORY_PROPS}
      {...patch}
    />
  );
}

/** The teams it is shared with, owners marked; share asks for confirmation first. */
export const Teams: Story = {
  render: () => <Sharing />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Owner")).toBeInTheDocument();
    await expect(canvas.getByText("Editor")).toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: /Finance/ }));
    await userEvent.click(canvas.getByRole("button", { name: "Share" }));
    const body = within(canvasElement.ownerDocument.body);
    const dialog = within(await body.findByRole("alertdialog"));
    await expect(dialog.getByText(/sign in again/)).toBeInTheDocument();
    await userEvent.click(dialog.getByRole("button", { name: "Share" }));
    await expect(onShare).toHaveBeenCalled();
  },
};

/** Stop sharing confirms, then sends nothing more than the team. */
export const Unshare: Story = {
  render: () => <Sharing />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const row = within(canvas.getByRole("row", { name: /medops/ }));
    await userEvent.click(row.getByRole("button", { name: /row actions/ }));
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(await body.findByRole("menuitem", { name: "Stop sharing" }));
    const dialog = within(await body.findByRole("alertdialog"));
    await userEvent.click(dialog.getByRole("button", { name: "Stop sharing" }));
    await expect(onUnshare).toHaveBeenCalledWith({ team: "medops", access: "viewer" });
  },
};

/** Without app.update the form is shown disabled with the permission it takes, and rows have no actions. */
export const ReadOnly: Story = {
  render: () => <Sharing canShare={false} canShareAsEditor={false} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole("button", { name: /row actions/ })).not.toBeInTheDocument();
  },
};

/** Without app.deploy, editor cannot be granted. */
export const ViewerOnly: Story = { render: () => <Sharing canShareAsEditor={false} /> };

/** An org rule asks for approval too. */
export const NeedsApproval: Story = {
  render: () => <Sharing ask={{ ...DEFAULT_ASK, approval: true, minimumApprovals: 2 }} />,
};

/** The last share was refused; the request to follow is linked. */
export const Refused: Story = {
  render: () => (
    <Sharing
      refusal={{
        message:
          "Share a semantic layer needs approval from 1 approver; run it again once it is approved.",
        approval: true,
      }}
    />
  ),
};

export const NotShared: Story = { render: () => <Sharing shares={[]} /> };

/** Shares, refusals and an agent's unshare, newest first. */
export const History: Story = {
  render: () => <Sharing section="history" />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Refused: STEP_UP_REQUIRED")).toBeInTheDocument();
    await expect(canvas.getByText("agent token")).toBeInTheDocument();
    await expect(
      canvas.getByText(/Then shared with medops \(viewer\), sales \(deployer\)/)
    ).toBeInTheDocument();
  },
};

/** Reading the history needs audit_log.read. */
export const HistoryDenied: Story = {
  render: () => <Sharing section="history" history={{ ...HISTORY_PROPS, allowed: false }} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("audit_log.read")).toBeInTheDocument();
  },
};

export const HistoryLoading: Story = {
  render: () => (
    <Sharing section="history" history={{ ...HISTORY_PROPS, items: [], loading: true }} />
  ),
};

export const HistoryFailed: Story = {
  render: () => (
    <Sharing section="history" history={{ ...HISTORY_PROPS, items: [], error: "Network error" }} />
  ),
};

export const HistoryEmpty: Story = {
  render: () => <Sharing section="history" history={{ ...HISTORY_PROPS, items: [] }} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Sharing />
    </div>
  ),
};
