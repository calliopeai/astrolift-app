import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { AppDetailsStepView } from "./AppDetailsStep";
import {
  DETAILS,
  DETAILS_STATE,
  LONG_DETAILS,
  LONG_DETAILS_STATE,
} from "./apps-list-wizard-shell.fixtures";
import type { AppDetailsFields } from "./use-app-details-step";

const meta: Meta = {
  title: "Screens/Apps/New/AppDetailsStep",
};
export default meta;

type Story = StoryObj;

/** Holds the wizard slice the step writes to, so the fields are typeable. */
function Stateful({ initial, details }: { initial: AppDetailsFields; details: typeof DETAILS }) {
  const [state, setState] = React.useState(initial);
  return <AppDetailsStepView {...details} state={state} setState={setState} />;
}

export const Full: Story = {
  render: () => <Stateful initial={DETAILS_STATE} details={DETAILS} />,
};

/** Teams and projects not back yet: the closest real "loading" state. */
export const Loading: Story = {
  render: () => (
    <Stateful
      initial={{ ...DETAILS_STATE, projectId: "" }}
      details={{ ...DETAILS, allTeams: [], allProjects: [], projectsLoading: true }}
    />
  ),
};

/** No projects the viewer can register into. */
export const Empty: Story = {
  render: () => (
    <Stateful
      initial={{ ...DETAILS_STATE, projectId: "" }}
      details={{ ...DETAILS, allProjects: [] }}
    />
  ),
};

/** The slug fails validation: the closest real "error" state. */
export const InvalidSlug: Story = {
  render: () => (
    <Stateful
      initial={{ ...DETAILS_STATE, slug: "API_Gateway!", slugTouched: true }}
      details={{ ...DETAILS, slugValid: false }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => <Stateful initial={LONG_DETAILS_STATE} details={LONG_DETAILS} />,
};
