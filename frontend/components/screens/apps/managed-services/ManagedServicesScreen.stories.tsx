import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { ManagedServiceMetricsPanel } from "@/components/observability/ManagedServiceMetricsPanel";

import {
  EMAIL_CONFIG,
  EMAIL_SHEET,
  ENVS,
  LONG_SERVICES,
  METRICS,
  SCREEN,
} from "./app-managed-services.fixtures";
import { EmailDetailSheetView } from "./EmailDetailSheet";
import { ManagedServicesScreen, ProvisionSheet } from "./ManagedServicesScreen";
import { APP_MANAGED_SERVICES_LIST } from "./managed-services-list";
import { ServiceDetailSheet } from "./ServiceDetailSheet";
import type { ManagedService } from "./use-managed-services";

const meta: Meta = {
  title: "Screens/Apps/ManagedServices/ManagedServicesScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** The slots the route fills with containers; stories fill them with views. */
const slots = {
  renderEmailDetail: (svc: ManagedService, onOpenChange: (next: boolean) => void) => (
    <EmailDetailSheetView
      {...EMAIL_SHEET}
      serviceName={svc.name}
      serviceConfig={svc.config ?? EMAIL_CONFIG}
      onOpenChange={onOpenChange}
    />
  ),
  renderServiceDetail: (svc: ManagedService | null, onOpenChange: (next: boolean) => void) => (
    <ServiceDetailSheet
      service={svc}
      onOpenChange={onOpenChange}
      metrics={<ManagedServiceMetricsPanel {...METRICS} />}
    />
  ),
};

/** The screen with in-memory list state and the slots filled with views. */
function Screen({ initial, ...props }: Partial<typeof SCREEN> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(APP_MANAGED_SERVICES_LIST, initial);
  return <ManagedServicesScreen {...SCREEN} {...slots} {...props} list={list} />;
}

export const Full: Story = {
  render: () => <Screen />,
};

export const Loading: Story = {
  render: () => <Screen rows={[]} loading totalCount={null} />,
};

export const Empty: Story = {
  render: () => <Screen rows={[]} totalCount={0} />,
};

/** A search that matches nothing: the filtered empty state. */
export const NoMatches: Story = {
  render: () => <Screen rows={[]} totalCount={0} initial={{ q: "kafka" }} />,
};

export const LoadFailed: Story = {
  render: () => (
    <Screen rows={[]} error={{ message: "Network error: failed to fetch managed services" }} />
  ),
};

/** More services than one page: Older is live on the cursor. */
export const MorePages: Story = {
  render: () => <Screen nextCursor="cursor-2" totalCount={60} />,
};

/** A mutation in flight: the row delete buttons are disabled. */
export const Busy: Story = {
  render: () => <Screen busy deprovisioning />,
};

export const LongStrings: Story = {
  render: () => (
    <Screen
      slug="storefront-with-a-deliberately-long-app-slug-for-overflow"
      rows={LONG_SERVICES}
      totalCount={1}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6): the table scrolls in its frame. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen rows={LONG_SERVICES} totalCount={1} />
    </div>
  ),
};

export const Provision: Story = {
  render: () => (
    <ProvisionSheet
      open
      onOpenChange={() => {}}
      envs={ENVS}
      onSubmit={async () => true}
      busy={false}
    />
  ),
};

/** No environments yet: nothing to pick, so submit stays disabled. */
export const ProvisionNoEnvironments: Story = {
  render: () => (
    <ProvisionSheet
      open
      onOpenChange={() => {}}
      envs={[]}
      onSubmit={async () => true}
      busy={false}
    />
  ),
};

export const Provisioning: Story = {
  render: () => (
    <ProvisionSheet open onOpenChange={() => {}} envs={ENVS} onSubmit={async () => true} busy />
  ),
};
