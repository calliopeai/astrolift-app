import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { DnsRecordsCard, type DnsRecordsCardData } from "@/components/observability/DnsRecordsCard";

const meta: Meta = { title: "Patterns/Observability/DnsRecordsCard" };
export default meta;

const data = (
  records: DnsRecordsCardData["astroliftAppDnsRecords"]["records"],
  reason = "OK"
): DnsRecordsCardData => ({ astroliftAppDnsRecords: { reason, records } }) as DnsRecordsCardData;

const BASE = { appSlug: "checkout", loading: false, onRefresh: () => {} };

export const Records: StoryObj = {
  render: () => (
    <DnsRecordsCard
      {...BASE}
      data={data([
        {
          name: "checkout.astro.example.com",
          type: "A",
          value: "10.0.4.12",
          ttl: 300,
          propagationStatus: "propagated",
        },
        {
          name: "pr-142.checkout.astro.example.com",
          type: "CNAME",
          value: "edge.astro.example.com",
          ttl: 60,
          propagationStatus: "pending",
        },
      ] as never)}
    />
  ),
};
export const NotConfigured: StoryObj = {
  render: () => <DnsRecordsCard {...BASE} data={data([], "NOT_CONFIGURED")} />,
};
export const Loading: StoryObj = { render: () => <DnsRecordsCard {...BASE} data={null} loading /> };
