import { LONG_PREVIEW, PREVIEWS } from "@/components/screens/pipelines/pipelines-previews.fixtures";

import type { PreviewDetailScreenProps } from "./PreviewDetail";
import type { PreviewsScreenProps } from "./PreviewsScreen";

/** Hand-typed fixtures for /previews and /previews/[id]. */

export { LONG_PREVIEW, PREVIEWS };

const noop = () => {};

/** Everything PreviewsScreen takes except the list state, which a story makes. */
export function previewsProps(
  overrides: Partial<Omit<PreviewsScreenProps, "list">> = {}
): Omit<PreviewsScreenProps, "list"> {
  return {
    rows: PREVIEWS,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    nextCursor: null,
    totalCount: PREVIEWS.length,
    canTearDown: true,
    tearingDown: false,
    tearDown: async () => {},
    ...overrides,
  };
}

export function previewDetailProps(
  extra: Partial<PreviewDetailScreenProps> = {}
): PreviewDetailScreenProps {
  return {
    id: PREVIEWS[0].id,
    preview: PREVIEWS[0],
    loading: false,
    error: null,
    onRetry: noop,
    runtimeLoading: false,
    onLoadRuntime: noop,
    logs: null,
    logsError: null,
    logsLoading: false,
    logsRequested: false,
    onLoadLogs: noop,
    deployments: null,
    deploymentsError: null,
    deploymentsLoading: false,
    deploymentsRequested: false,
    onLoadDeployments: noop,
    ...extra,
  };
}
