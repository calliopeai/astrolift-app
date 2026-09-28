import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
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

export const Full: Story = {
  render: () => <ManagedServicesScreen {...SCREEN} {...slots} />,
};

export const Loading: Story = {
  render: () => (
    <ManagedServicesScreen
      {...SCREEN}
      {...slots}
      table={fakeController<ManagedService>({ state: "loading" })}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <ManagedServicesScreen
      {...SCREEN}
      {...slots}
      table={fakeController<ManagedService>({ state: "empty", rows: [], totalCount: 0 })}
    />
  ),
};

/** A search that matches nothing: the filtered empty state. */
export const NoMatches: Story = {
  render: () => (
    <ManagedServicesScreen
      {...SCREEN}
      {...slots}
      table={fakeController<ManagedService>({
        state: "emptyFiltered",
        rows: [],
        totalCount: 0,
        search: "kafka",
        isFiltered: true,
      })}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <ManagedServicesScreen
      {...SCREEN}
      {...slots}
      table={fakeController<ManagedService>({
        state: "error",
        error: new Error("Network error: failed to fetch managed services"),
      })}
    />
  ),
};

/** A mutation in flight: the row delete buttons are disabled. */
export const Busy: Story = {
  render: () => <ManagedServicesScreen {...SCREEN} {...slots} busy deprovisioning />,
};

export const LongStrings: Story = {
  render: () => (
    <ManagedServicesScreen
      {...SCREEN}
      {...slots}
      slug="storefront-with-a-deliberately-long-app-slug-for-overflow"
      table={fakeController<ManagedService>({ rows: LONG_SERVICES, totalCount: 1 })}
    />
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
