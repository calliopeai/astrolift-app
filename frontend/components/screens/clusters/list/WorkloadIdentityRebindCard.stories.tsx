import { NextIntlClientProvider } from "next-intl";
import german from "@/messages/de.json";
import japanese from "@/messages/ja.json";
import { expect, within } from "storybook/test";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { REBIND_FAILED, rebind } from "./fixtures";
import { WorkloadIdentityRebindCard } from "./WorkloadIdentityRebindCard";

const meta: Meta = {
  title: "Screens/Clusters/List/WorkloadIdentityRebindCard",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Recorded by the registration that re-pointed the cluster, not started yet. */
export const Pending: Story = {
  render: () => (
    <WorkloadIdentityRebindCard
      rebind={rebind({ state: "pending", appsTotal: 0, appsReconciled: 0 })}
    />
  ),
};

export const Running: Story = {
  render: () => <WorkloadIdentityRebindCard rebind={rebind()} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("7 of 12")).toBeVisible();
  },
};

export const Succeeded: Story = {
  render: () => (
    <WorkloadIdentityRebindCard
      rebind={rebind({
        state: "succeeded",
        appsReconciled: 12,
        finishedAt: "2026-10-18T11:41:30Z",
      })}
    />
  ),
};

/** Each app whose role did not move, with the cloud's reason; long ARNs wrap. */
export const Failed: Story = {
  render: () => <WorkloadIdentityRebindCard rebind={REBIND_FAILED} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("2 roles did not move")).toBeVisible();
    await expect(canvas.getByText(/not managed by the platform/)).toBeVisible();
  },
};

/** Stopped before reaching the apps: the cluster's issuer changed again. */
export const Refused: Story = {
  render: () => (
    <WorkloadIdentityRebindCard
      rebind={rebind({
        state: "failed",
        appsTotal: 0,
        appsReconciled: 0,
        finishedAt: "2026-10-18T11:40:05Z",
        error:
          "the cluster's OIDC issuer is oidc.eks.eu-west-1.amazonaws.com/id/0A1B2C3D, not oidc.eks.us-west-2.amazonaws.com/id/8D5B4A1C0F2E9B7A6C3D1E0F9A8B7C6D; register the cluster again to move its roles",
      })}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <WorkloadIdentityRebindCard rebind={REBIND_FAILED} />
    </div>
  ),
};

export const GermanWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="de" messages={german}>
      <div style={{ width: 768 }}>
        <WorkloadIdentityRebindCard rebind={REBIND_FAILED} />
      </div>
    </NextIntlClientProvider>
  ),
};

export const JapaneseRunning: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={japanese}>
      <WorkloadIdentityRebindCard rebind={rebind()} />
    </NextIntlClientProvider>
  ),
};
