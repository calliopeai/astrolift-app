import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import { COMPARE, DEPLOY_LONG, json, LONG } from "./app-deployments-logs.fixtures";
import { CompareDeploymentsSheetView } from "./CompareDeploymentsSheet";

const meta: Meta = { title: "Screens/Apps/Deployments/CompareDeploymentsSheet" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <CompareDeploymentsSheetView {...COMPARE} /> };

export const Loading: Story = {
  render: () => <CompareDeploymentsSheetView {...COMPARE} comparison={null} loading />,
};

/** Identical manifests and no image diff. */
export const Empty: Story = {
  render: () => (
    <CompareDeploymentsSheetView
      {...COMPARE}
      comparison={{ ...COMPARE.comparison!, imageDiffSummary: "", manifestDiff: [] }}
    />
  ),
};

export const QueryError: Story = {
  render: () => (
    <CompareDeploymentsSheetView
      {...COMPARE}
      comparison={null}
      error="Deployments belong to different apps."
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <CompareDeploymentsSheetView
      {...COMPARE}
      deployA={DEPLOY_LONG}
      comparison={{
        ...COMPARE.comparison!,
        imageDiffSummary: `${LONG} ${LONG}`,
        manifestDiff: [
          {
            op: "replace",
            path: `/Deployment/${LONG}/spec/template`,
            before: json(LONG),
            after: json(LONG),
          },
        ],
      }}
    />
  ),
};

export const JapaneseWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <div style={{ width: 768 }}>
        <CompareDeploymentsSheetView {...COMPARE} />
      </div>
    </NextIntlClientProvider>
  ),
};
