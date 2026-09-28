import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppDoctorPanelView } from "./AppDoctorPanel";
import { DOCTOR_IDLE, DOCTOR_REPORT, LONG } from "./app-homes.fixtures";

const meta: Meta = { title: "Screens/Apps/Homes/AppDoctorPanel" };
export default meta;

type Story = StoryObj;

/** Before the operator asks: the panel never probes on mount. */
export const Idle: Story = { render: () => <AppDoctorPanelView {...DOCTOR_IDLE} /> };

export const Loading: Story = {
  render: () => <AppDoctorPanelView {...DOCTOR_IDLE} called loading />,
};

export const Full: Story = { render: () => <AppDoctorPanelView {...DOCTOR_REPORT} /> };

export const Healthy: Story = {
  render: () => (
    <AppDoctorPanelView
      {...DOCTOR_REPORT}
      report={{
        healthy: true,
        checks: (DOCTOR_REPORT.report?.checks ?? []).map((c) => ({
          ...c,
          status: "pass",
          fix: "",
        })),
      }}
    />
  ),
};

export const Empty: Story = {
  render: () => <AppDoctorPanelView {...DOCTOR_REPORT} report={{ healthy: true, checks: [] }} />,
};

export const ProbeError: Story = {
  render: () => (
    <AppDoctorPanelView
      {...DOCTOR_IDLE}
      called
      errorMessage="Response not successful: Received status code 502"
    />
  ),
};

export const Repairing: Story = {
  render: () => <AppDoctorPanelView {...DOCTOR_REPORT} running="retry_autowire" />,
};

export const LongStrings: Story = {
  render: () => (
    <AppDoctorPanelView
      {...DOCTOR_REPORT}
      report={{
        healthy: false,
        checks: [
          { key: LONG, status: "fail", detail: `${LONG} ${LONG}`, fix: "resync_manifest" },
          { key: "dns", status: "warn", detail: LONG.repeat(3), fix: "rerun_onboarding" },
        ],
      }}
    />
  ),
};
