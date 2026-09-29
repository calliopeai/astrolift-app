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

const LONG_NAME = `${"a".repeat(60)}.checkout.astro.example.com`;
const LONG_VALUE =
  "arn:aws:elasticloadbalancing:us-west-2:123456789012:loadbalancer/net/astrolift-ingress/" +
  "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

const MANY = Array.from({ length: 30 }, (_, i) => ({
  name: `svc-${i}.checkout.astro.example.com`,
  type: i % 3 === 0 ? "CNAME" : "A",
  value: `10.0.4.${i}`,
  ttl: 300,
  propagationStatus: i % 4 === 0 ? "pending" : "propagated",
}));

/** Thirty records: the embedded list pages them. */
export const ManyRecords: StoryObj = {
  render: () => <DnsRecordsCard {...BASE} data={data(MANY as never)} />,
};

export const ErrorState: StoryObj = {
  render: () => <DnsRecordsCard {...BASE} data={data([], "ERROR")} />,
};

export const LongStrings: StoryObj = {
  render: () => (
    <DnsRecordsCard
      {...BASE}
      data={data([
        {
          name: LONG_NAME,
          type: "CNAME",
          value: LONG_VALUE,
          ttl: 60,
          propagationStatus: "pending",
        },
      ] as never)}
    />
  ),
};

export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <DnsRecordsCard {...BASE} data={data(MANY as never)} />
    </div>
  ),
};
