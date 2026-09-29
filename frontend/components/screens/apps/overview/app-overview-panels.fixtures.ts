import { DEPLOY_FAILURE, LONG_ARN, LONG_SHA, LONG_URL } from "@/components/panel/fixtures";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { ACCESSES, LONG_ACCESS } from "./app-overview-cards-a.fixtures";
import type { LatestDeployPanelProps } from "./LatestDeployPanel";
import type { OwnershipPanelProps } from "./OwnershipPanel";

/** Hand-typed fixtures for the Overview's own panels (spec 44 §5.2). */

const noop = async () => {};

export const LONG =
  "platform-team-shared-production-checkout-service-with-a-deliberately-long-name-that-keeps-going";

function deploy(over: Partial<AstroliftDeployment>): AstroliftDeployment {
  return {
    id: "dep-1112015d",
    status: "running",
    imageTag: "sha-1112015d9f3c",
    environmentName: "production",
    workloadSlug: "web",
    triggerKind: "push",
    commitSha: "1112015d9f3c2a1b7e4d",
    commitMessage: "Serve the checkout page from the edge cache\n\nLonger body.",
    commitAuthor: "leo",
    ciActorKind: "",
    createdAt: "2026-09-28T11:40:00Z",
    startedAt: "2026-09-28T11:40:05Z",
    endedAt: "2026-09-28T11:42:17Z",
    durationSeconds: 132,
    statusReason: "",
    buildError: "",
    abortedReason: "",
    manifestResyncError: "",
    ...over,
  } as AstroliftDeployment;
}

export const DEPLOY_OK = deploy({});
export const DEPLOY_FAILED = deploy({
  id: "dep-2a9c",
  status: "failed",
  statusReason: DEPLOY_FAILURE,
  durationSeconds: 48,
});
export const DEPLOY_IN_FLIGHT = deploy({
  id: "dep-3b7e",
  status: "deploying",
  endedAt: null,
  durationSeconds: null,
});
export const DEPLOY_LONG = deploy({
  id: LONG_SHA,
  status: "failed",
  imageTag: LONG_SHA,
  environmentName: LONG,
  workloadSlug: LONG,
  commitMessage: LONG_URL,
  commitAuthor: LONG,
  statusReason: LONG_ARN,
});

export const LATEST_DEPLOY: LatestDeployPanelProps = {
  loading: false,
  current: DEPLOY_OK,
  lastGood: undefined,
  rolling: false,
  onRollback: noop,
  appHref: "/apps/checkout",
};

export const LATEST_DEPLOY_FAILED: LatestDeployPanelProps = {
  ...LATEST_DEPLOY,
  current: DEPLOY_FAILED,
  lastGood: DEPLOY_OK,
};

export const OWNERSHIP: OwnershipPanelProps = {
  loading: false,
  accesses: ACCESSES,
  error: null,
  onRetry: () => {},
  projectName: "Storefront",
  teamName: "Platform",
  settingsHref: "/apps/checkout/settings",
};

export const OWNERSHIP_LONG: OwnershipPanelProps = {
  ...OWNERSHIP,
  accesses: [...ACCESSES, LONG_ACCESS],
  projectName: LONG,
  teamName: LONG,
};
