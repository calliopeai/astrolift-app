import type { AppFrameApp, AppFrameProps } from "./AppFrame";
import { LONG } from "./app-detail-shell.fixtures";

/** Hand-typed fixtures for the app frame: the header data and its callbacks. */

const noop = async () => {};

export const FRAME_APP: AppFrameApp = {
  id: "8d0f7c2e-4b1a-4e5f-9a3c-2f6b7d8e9a01",
  slug: "checkout",
  name: "checkout",
  status: "ready",
  live: true,
  archived: false,
  failureReason: null,
  environment: { name: "production", clusterSlug: "conflict-astrolift" },
  moreEnvironments: 2,
  appUrl: "https://checkout.astrolift.example.com",
  repoUrl: "https://github.com/acme/checkout",
};

export const FRAME: Omit<AppFrameProps, "children"> = {
  slug: "checkout",
  pathname: "/apps/checkout",
  app: FRAME_APP,
  loading: false,
  error: null,
  onRetry: () => {},
  latestDeploy: { imageTag: "sha-1112015d", environmentName: "production" },
  canDeploy: true,
  deploying: false,
  onDeploy: noop,
  canUpdate: true,
  archiving: false,
  onArchive: noop,
  onRestore: noop,
  canDelete: true,
  deleting: false,
  onDelete: noop,
  onCopyId: () => {},
};

export const FAILING_APP: AppFrameApp = {
  ...FRAME_APP,
  status: "failed",
  live: false,
  appUrl: null,
  failureReason:
    "Image pull denied: 123456789012.dkr.ecr.us-west-2.amazonaws.com/checkout:sha-1112015d9f3c2a1b7e4d5c6b7a8f9e0d1c2b3a4f5e6d7c8b9a0f1e2d3c4b5a6f7e8d9c0b is not authorized for arn:aws:iam::123456789012:role/astrolift-node-role-conflict-astrolift-production-us-west-2-with-a-deliberately-long-suffix-for-overflow-testing",
};

export const LONG_FRAME_APP: AppFrameApp = {
  ...FRAME_APP,
  slug: LONG,
  name: LONG,
  environment: {
    name: "production-eu-central-with-a-long-environment-name",
    clusterSlug: `cluster-${LONG}`,
  },
  appUrl: `https://${LONG}.astrolift.example.com/and/an/unbroken/path/that/keeps/going/without/spaces`,
};
