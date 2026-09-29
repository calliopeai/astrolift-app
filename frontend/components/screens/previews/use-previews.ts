"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useListState } from "@/components/list/use-list-state";
import { TEAR_DOWN_PREVIEW } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_PREVIEW_ENVIRONMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { PREVIEWS_LIST, previewsVariables } from "./previews-list";

interface MutationResultLite {
  ok: boolean;
  errors: { code: string; message: string }[];
}

interface PreviewsPageResp {
  astroliftPreviewEnvironmentsPage: CursorPage<AstroliftPreviewEnvironment>;
}

/**
 * Apps › Previews: URL list state, one cursor page of every app's previews,
 * and teardown. The 30s poll is kept: a building preview settles while an
 * operator watches. The data half of PreviewsScreen.
 */
export function usePreviews() {
  const { can } = useMyPermissions();
  const list = useListState(PREVIEWS_LIST);
  const { state } = list;
  const query = useQuery<PreviewsPageResp>(LIST_PREVIEW_ENVIRONMENTS_PAGE, {
    variables: previewsVariables(list.filters, state),
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftPreviewEnvironmentsPage;

  const [tearDownMutation, tearState] = useMutation<{
    tearDownPreview: MutationResultLite;
  }>(TEAR_DOWN_PREVIEW, { refetchQueries: ["ListPreviewEnvironmentsPage"] });

  /** Throws on failure so the confirm dialog shows the error inline. */
  async function tearDown(preview: AstroliftPreviewEnvironment) {
    const { data: result } = await tearDownMutation({
      variables: { input: { id: preview.id } },
    });
    const r = result?.tearDownPreview;
    if (r?.ok) {
      toast.success(`Teardown enqueued`);
    } else {
      throw new Error(r?.errors[0]?.message ?? "Teardown failed");
    }
  }

  return {
    list,
    rows: page?.items ?? [],
    loading: query.loading && !data,
    stale: query.loading && !query.data && Boolean(data),
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
    nextCursor: page?.nextCursor ?? null,
    totalCount: page?.totalCount ?? null,
    canTearDown: can("app.deploy"),
    tearingDown: tearState.loading,
    tearDown,
  };
}
