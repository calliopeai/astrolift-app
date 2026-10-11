import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import en from "@/messages/en.json";
import { DomainRecordRouting, type DomainRecordRoutingData } from "./DomainRecordRouting";

export const ROUTING_EXAMPLES: DomainRecordRoutingData[] = [
  { policy: "SIMPLE", reason: "PROVIDER_METADATA_OBSERVED" },
  { policy: "WEIGHTED", reason: "PROVIDER_METADATA_OBSERVED", setIdentifier: "primary", weight: 0 },
  {
    policy: "LATENCY",
    reason: "PROVIDER_METADATA_OBSERVED",
    setIdentifier: "west",
    region: "us-west-2",
  },
  {
    policy: "FAILOVER",
    reason: "PROVIDER_METADATA_OBSERVED",
    setIdentifier: "backup",
    failover: "SECONDARY",
    healthCheckId: "health-reference",
  },
  {
    policy: "GEOLOCATION",
    reason: "PROVIDER_METADATA_OBSERVED",
    setIdentifier: "california",
    geoLocation: { continentCode: null, countryCode: "US", subdivisionCode: "CA" },
  },
  {
    policy: "MULTIVALUE",
    reason: "PROVIDER_METADATA_OBSERVED",
    setIdentifier: "answer-a",
    multiValueAnswer: true,
  },
  {
    policy: "GEOPROXIMITY",
    reason: "PROVIDER_METADATA_OBSERVED",
    setIdentifier: "coordinates",
    geoProximity: {
      awsRegion: null,
      localZoneGroup: null,
      coordinates: { latitude: "-90", longitude: "180.00" },
      bias: -99,
    },
  },
  {
    policy: "CIDR",
    reason: "PROVIDER_METADATA_OBSERVED",
    setIdentifier: "default-network",
    cidr: { collectionId: "12345678-1234-1234-1234-123456789abc", locationName: "*" },
    trafficPolicyInstanceId: "traffic-reference",
  },
  { policy: "UNKNOWN", reason: "INCOMPLETE_ROUTING_POLICY", setIdentifier: "unclassified" },
];
const meta: Meta<typeof DomainRecordRouting> = {
  title: "Screens/Domains/DomainRecordRouting",
  component: DomainRecordRouting,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  args: { routing: ROUTING_EXAMPLES[1] },
};
export default meta;
type Story = StoryObj<typeof meta>;
export const WeightedZero: Story = {};
export const PolicyExamples: Story = {
  render: () => (
    <div className="max-w-2xl space-y-4">
      {ROUTING_EXAMPLES.map((routing) => (
        <DomainRecordRouting key={routing.policy} routing={routing} />
      ))}
    </div>
  ),
};
export const LongIdentifier: Story = {
  args: { routing: { ...ROUTING_EXAMPLES[1], setIdentifier: "regional-target-".repeat(8) } },
};
export const ConflictingMetadata: Story = {
  args: {
    routing: {
      policy: "UNKNOWN",
      reason: "AMBIGUOUS_ROUTING_POLICY",
      weight: 5,
      region: "us-west-2",
      setIdentifier: "ambiguous",
    },
  },
};
export const Absent: Story = { args: { routing: null } };
