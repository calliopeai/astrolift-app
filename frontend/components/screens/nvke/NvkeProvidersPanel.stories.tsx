import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeProvidersPanel, type NvkeProvidersPanelProps } from "./NvkeProvidersPanel";
import { SITE, OPERATION } from "./fixtures";
export const PROVIDER_PROPS: NvkeProvidersPanelProps = {
  observation: {
    siteId: SITE.id,
    operationId: OPERATION.id,
    operationVersion: 12,
    sourceSiteVersion: 7,
    connectionId: "123e4567-e89b-12d3-a456-426614174091",
    connectionVersion: 3,
    upstreamRevision: SITE.upstreamRevision,
    observedAt: "2026-11-08T12:00:00.123456789Z",
    complete: true,
    providers: [
      {
        provider: "nico",
        state: "DEGRADED",
        reason: "provider_manager_unavailable",
        complete: true,
        crdsEstablished: true,
        controllerAvailable: false,
        managerNamespace: "capnico-system",
        managerName: "nico-manager",
        readyReplicas: "0",
        desiredReplicas: "1",
        secretNamespace: "capnico-system",
        secretName: "nico-credentials",
        secretPresent: true,
        endpointConfigured: null,
        unconfiguredIsValid: false,
        credentials: {
          health: "HEALTHY",
          reason: "provider_check_succeeded",
          identityNamespace: "capnico-system",
          identityName: "primary-nico",
          identityGeneration: "9007199254740993",
          readyStatus: "True",
          observedGeneration: "9007199254740993",
          lastCheckedTime: "2026-11-08T11:59:00.123456789Z",
          nextCheckTime: "2026-11-08T12:04:00.123456789Z",
          additionalIdentityCount: 31,
        },
      },
      {
        provider: "launchlayer",
        state: "NOT-CONFIGURED",
        reason: "provider_secret_absent",
        complete: true,
        crdsEstablished: true,
        controllerAvailable: true,
        managerNamespace: "capllaunchlayer-system",
        managerName: "launchlayer-manager",
        readyReplicas: "1",
        desiredReplicas: "1",
        secretNamespace: "capllaunchlayer-system",
        secretName: "launchlayer-credentials",
        secretPresent: false,
        endpointConfigured: true,
        unconfiguredIsValid: true,
        credentials: null,
      },
    ],
  },
  provider: "nico",
  identities: fakeController({
    rows: [
      { key: "capnico-system/additional-030", namespace: "capnico-system", name: "additional-030" },
    ],
    totalCount: 31,
    hasNext: true,
    sortEnabled: false,
  }),
  loading: false,
  error: null,
  onRefresh: () => {},
  onProviderChange: () => {},
};
export default {
  title: "Screens/NVKE/Infrastructure providers",
  component: NvkeProvidersPanel,
  excludeStories: ["PROVIDER_PROPS"],
  args: PROVIDER_PROPS,
} satisfies Meta<typeof NvkeProvidersPanel>;
type Story = StoryObj<typeof NvkeProvidersPanel>;
export const RecentCheckManagerDown: Story = {};
export const Partial: Story = {
  args: {
    observation: {
      ...PROVIDER_PROPS.observation!,
      complete: false,
      providers: PROVIDER_PROPS.observation!.providers.map((row) => ({
        ...row,
        state: "UNKNOWN",
        complete: false,
        crdsEstablished: null,
        controllerAvailable: null,
        secretPresent: null,
        credentials: null,
      })),
    },
  },
};
export const Empty: Story = { args: { observation: null } };
export const Loading: Story = { args: { loading: true } };
export const Failure: Story = { args: { error: "The historical observation could not be read." } };
export const IdentityFailure: Story = {
  args: { identities: fakeController({ state: "error", sortEnabled: false }) },
};
export const NotInstalled: Story = {
  args: {
    observation: {
      ...PROVIDER_PROPS.observation!,
      providers: PROVIDER_PROPS.observation!.providers.map((row) => ({
        ...row,
        state: "NOT-INSTALLED",
        crdsEstablished: false,
        controllerAvailable: null,
        managerNamespace: null,
        managerName: null,
        readyReplicas: null,
        desiredReplicas: null,
        secretNamespace: null,
        secretName: null,
        secretPresent: null,
        endpointConfigured: null,
        credentials: null,
      })),
    },
    identities: fakeController({ state: "empty", sortEnabled: false }),
  },
};
export const LongStrings: Story = {
  args: {
    observation: {
      ...PROVIDER_PROPS.observation!,
      providers: PROVIDER_PROPS.observation!.providers.map((row) => ({
        ...row,
        managerName: "long-provider-manager-".repeat(12),
        secretName: "long-secret-name-".repeat(14),
      })),
    },
  },
};
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeProvidersPanel {...args} />
    </div>
  ),
};
