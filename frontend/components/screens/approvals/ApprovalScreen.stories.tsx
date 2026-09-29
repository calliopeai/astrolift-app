import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ApprovalHistoryPanel } from "./ApprovalHistory";
import { APPROVAL, DEPLOYMENT, HISTORY, LONG } from "./approvals-a.fixtures";
import { ApprovalScreen } from "./ApprovalScreen";

const meta: Meta = {
  title: "Screens/Approvals/ApprovalScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const history = <ApprovalHistoryPanel {...HISTORY} />;

/** Pending approval, one of two sign-offs in, with commit provenance and history. */
export const Full: Story = {
  render: () => <ApprovalScreen {...APPROVAL} history={history} />,
};

export const Loading: Story = {
  render: () => <ApprovalScreen {...APPROVAL} deployment={null} loading />,
};

/**
 * No deployment (expired link, wrong id, or a failed query). The page has
 * no separate error state; this is what a query error renders too.
 */
export const NotFound: Story = {
  render: () => <ApprovalScreen {...APPROVAL} deployment={null} />,
};

/** No commit info and an empty history: the barest page. */
export const Empty: Story = {
  render: () => (
    <ApprovalScreen
      {...APPROVAL}
      deployment={{
        ...DEPLOYMENT,
        commitSha: "",
        commitMessage: "",
        commitAuthor: "",
        branch: "",
        imageDigest: "",
        clusterRevision: "",
        workloadSlug: null,
        approvedBy: [],
        awaitingApprovers: [],
        approvalsReceived: 0,
      }}
      history={<ApprovalHistoryPanel entries={[]} loading={false} />}
    />
  ),
};

/** The viewer triggered this deploy, so they cannot approve it. */
export const SelfTriggered: Story = {
  render: () => (
    <ApprovalScreen
      {...APPROVAL}
      deployment={{ ...DEPLOYMENT, triggeredByMe: true }}
      history={history}
    />
  ),
};

export const MissingPermission: Story = {
  render: () => <ApprovalScreen {...APPROVAL} canApprovePermission={false} history={history} />,
};

export const Busy: Story = {
  render: () => <ApprovalScreen {...APPROVAL} busy history={history} />,
};

export const AlreadyApproved: Story = {
  render: () => (
    <ApprovalScreen
      {...APPROVAL}
      deployment={{ ...DEPLOYMENT, status: "running", approvalsReceived: 2 }}
      history={history}
    />
  ),
};

export const Failed: Story = {
  render: () => (
    <ApprovalScreen
      {...APPROVAL}
      deployment={{ ...DEPLOYMENT, status: "failed" }}
      history={history}
    />
  ),
};

/** Rejected: the reason block shows and no action applies. */
export const Rejected: Story = {
  render: () => (
    <ApprovalScreen
      {...APPROVAL}
      deployment={{
        ...DEPLOYMENT,
        status: "pending",
        abortedReason: "Change freeze until the incident review closes.",
      }}
      history={history}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <ApprovalScreen
      {...APPROVAL}
      deployment={{
        ...DEPLOYMENT,
        registeredAppSlug: LONG,
        environmentName: LONG,
        imageTag: LONG,
        workloadSlug: LONG,
        branch: LONG,
        commitAuthor: LONG,
        commitMessage: `${LONG}\n${LONG}\n${LONG}\n${LONG}\n${LONG}`,
        abortedReason: LONG,
      }}
      history={history}
    />
  ),
};
