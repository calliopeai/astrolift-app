import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import { START, START_LONG } from "./deployments.fixtures";
import { StartDeploymentPage } from "./StartDeploymentPage";

const meta: Meta = {
  title: "Screens/Deployments/StartDeploymentPage",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/** Step 1, an app picked: its environments listed, with approvals and paused markers. */
export const Full: Story = { render: () => <StartDeploymentPage {...START} /> };

/** The app list has not arrived yet. */
export const Loading: Story = {
  render: () => (
    <StartDeploymentPage {...START} apps={[]} environments={[]} appSlug="" appsLoading />
  ),
};

/** An app with no environments: the picker is empty and says why. */
export const Empty: Story = {
  render: () => <StartDeploymentPage {...START} environments={[]} />,
};

/** The app list failed to load. */
export const ErrorState: Story = {
  render: () => (
    <StartDeploymentPage
      {...START}
      apps={[]}
      appSlug=""
      appsError="Network error: failed to fetch"
    />
  ),
};

/** Step 2 with the backend's refusal beside the tag field. */
export const FieldError: Story = {
  render: () => (
    <StartDeploymentPage
      {...START}
      initialStep={2}
      initialValues={{ environmentName: "prod", imageTag: "sha-nope" }}
      initialErrors={{ imageTag: "No image sha-nope in ghcr.io/example/storefront." }}
    />
  ),
};

/** Step 3: the review before anything deploys, into an environment that needs approvals. */
export const Review: Story = {
  render: () => (
    <StartDeploymentPage
      {...START}
      initialStep={3}
      initialValues={{
        environmentName: "prod",
        imageTag: "sha-4f2a9c1e7b",
        imageDigest: "sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",
      }}
    />
  ),
};

export const Submitting: Story = {
  render: () => (
    <StartDeploymentPage
      {...START}
      submitting
      initialStep={3}
      initialValues={{ environmentName: "stg", imageTag: "sha-4f2a9c1e7b" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <StartDeploymentPage
      {...START_LONG}
      initialStep={3}
      initialValues={{
        environmentName: `preview-${START_LONG.appSlug}`,
        imageTag: `sha-${"a".repeat(64)}`,
        imageDigest: `sha256:${"f".repeat(64)}`,
      }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <StartDeploymentPage {...START} />
    </div>
  ),
};

export const FrenchReview: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <StartDeploymentPage
        {...START}
        initialStep={3}
        initialValues={{ environmentName: START.environments[0].name, imageTag: "sha-literal" }}
        environments={[{ ...START.environments[0], requiredApprovals: 2, deploysPaused: true }]}
      />
    </NextIntlClientProvider>
  ),
};
export const JapaneseValidation: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <StartDeploymentPage {...START} appSlug="" />
    </NextIntlClientProvider>
  ),
};
