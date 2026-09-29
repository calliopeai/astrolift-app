import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  WorkloadIdentityCard,
  type WorkloadIdentityCardData,
} from "@/components/observability/WorkloadIdentityCard";

const meta: Meta = { title: "Patterns/Observability/WorkloadIdentityCard" };
export default meta;

const data = (binding: unknown, reason = "OK"): WorkloadIdentityCardData =>
  ({ astroliftAppIdentityBinding: { reason, binding } }) as WorkloadIdentityCardData;

const BASE = { appSlug: "checkout", loading: false, onRefresh: () => {} };

export const Bound: StoryObj = {
  render: () => (
    <WorkloadIdentityCard
      {...BASE}
      data={data({
        kind: "irsa",
        roleArnOrPrincipal: "arn:aws:iam::123456789012:role/conflict-checkout-workload",
        trustPolicySummary: "system:serviceaccount:storefront:checkout",
        lastUsedAt: "2026-09-28T11:40:00Z",
      })}
    />
  ),
};
export const Unbound: StoryObj = {
  render: () => <WorkloadIdentityCard {...BASE} data={data(null, "NO_DATA_YET")} />,
};
