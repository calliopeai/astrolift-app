import {
  ENV_LONG,
  ENV_PROD,
  ENV_STAGING_PAUSED,
  ENV_UNPLACED,
  LONG,
} from "@/components/screens/domains/domains-environments.fixtures";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { EnvironmentDetailProps } from "./EnvironmentDetail";
import type { EnvironmentsScreenProps } from "./EnvironmentsScreen";

/** Hand-typed fixtures for /environments and /environments/[id]. */

export { ENV_LONG, ENV_PROD, ENV_STAGING_PAUSED, ENV_UNPLACED, LONG };

const noop = () => {};
const noopAsync = async () => {};

/** A preview environment, for the Previews view. */
export const ENV_PREVIEW: AstroliftAppEnvironment = {
  ...ENV_PROD,
  id: "e0000000-0000-4000-8000-000000000005",
  name: "preview-pr-412",
  url: "https://pr-412-storefront.pr.acme.example",
  requiredApprovals: 0,
  settings: [],
};

export const ALL_ENVIRONMENTS: AstroliftAppEnvironment[] = [
  ENV_PROD,
  ENV_STAGING_PAUSED,
  ENV_UNPLACED,
  ENV_PREVIEW,
];

/** Everything EnvironmentsScreen takes except the list state, which a story makes. */
export function environmentsProps(
  overrides: Partial<Omit<EnvironmentsScreenProps, "list">> = {}
): Omit<EnvironmentsScreenProps, "list"> {
  return {
    appSlug: null,
    rows: [],
    totalCount: 0,
    loading: false,
    error: null,
    onRetry: noop,
    busy: false,
    canPause: true,
    onPause: noopAsync,
    onResume: noopAsync,
    ...overrides,
  };
}

export const ENVIRONMENT: EnvironmentDetailProps = {
  id: ENV_PROD.id,
  loading: false,
  environment: ENV_PROD,
  error: null,
  onRetry: noop,
};
