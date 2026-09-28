"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { TEAR_DOWN_PREVIEW } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_PREVIEW_ENVIRONMENTS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite {
  ok: boolean;
  errors: { code: string; message: string }[];
}

interface PreviewsPageResp {
  astroliftPreviewEnvironmentsPage: CursorPage<AstroliftPreviewEnvironment>;
}

/** The data half of PreviewsScreen: the preview table walk and teardown. */
export function usePreviews() {
  const { can } = useMyPermissions();

  // `appSlug: null` is every app, which is what this page is. The field
  // searches app, branch, hostname, commit and status, and takes no sort
  // argument, so no column declares a `sortKey`. The 30s poll is kept:
  // a building preview settles while an operator watches this page.
  const table = useCursorTable<AstroliftPreviewEnvironment>({
    query: LIST_PREVIEW_ENVIRONMENTS_PAGE,
    variables: { appSlug: null },
    extract: (d) => (d as PreviewsPageResp | undefined)?.astroliftPreviewEnvironmentsPage,
    searchVariable: "search",
    urlKey: "pv",
    pollInterval: 30000,
  });

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
    table,
    canTearDown: can("app.deploy"),
    tearingDown: tearState.loading,
    tearDown,
  };
}
