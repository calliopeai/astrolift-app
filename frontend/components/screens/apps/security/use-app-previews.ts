"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable } from "@/components/data-table";
import {
  CREATE_PREVIEW_ENVIRONMENT,
  EXTEND_PREVIEW_TTL,
  TEAR_DOWN_PREVIEW,
} from "@/graphql/lifecycle/lifecycle.mutations";
import {
  LIST_PREVIEW_ENVIRONMENTS,
  LIST_PREVIEW_ENVIRONMENTS_PAGE,
} from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftPreviewEnvironment,
  PreviewTtlExtendDays,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface PreviewsResp {
  astroliftPreviewEnvironments: AstroliftPreviewEnvironment[];
}
interface PreviewsPageResp {
  astroliftPreviewEnvironmentsPage: {
    items: AstroliftPreviewEnvironment[];
    nextCursor?: string | null;
    totalCount?: number | null;
  };
}
interface MutationResultLite {
  ok: boolean;
  errors: { code: string; message: string }[];
}

// Stale-after threshold: anything still running this long after its last
// deploy is a candidate for cleanup. 7d is the default "feature work that
// should have merged by now" window — orgs that want longer can ignore
// the CTA, orgs that want shorter aren't penalized.
export const STALE_DAYS = 7;
const STALE_THRESHOLD_MS = STALE_DAYS * 24 * 60 * 60 * 1000;

/**
 * Both the teardown and the TTL-extend paths refresh two documents: the
 * table's paginated one by operation name, so it re-runs with the cursor
 * and search currently in effect, and the flat summary query the spend
 * roll-up and stale sweep read.
 */
function refetchFor(appSlug: string) {
  return [
    "ListPreviewEnvironmentsPage",
    { query: LIST_PREVIEW_ENVIRONMENTS, variables: { appSlug } },
  ];
}

export function isStale(p: AstroliftPreviewEnvironment): boolean {
  if (p.status !== "running") return false;
  if (!p.lastDeployedAt) return false;
  const last = new Date(p.lastDeployedAt).getTime();
  return Date.now() - last > STALE_THRESHOLD_MS;
}

/**
 * The app behind the previews tab, its whole preview set (for the summary
 * chrome), the server-paged table, and the create / extend / tear-down
 * mutations. The data half of AppPreviewsScreen.
 */
export function useAppPreviews(slug: string) {
  const t = useTranslations("apps.previews");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });

  /**
   * Summary chrome — the status counts, the stale sweep (#431) and the
   * monthly spend roll-up (#660) — answers questions about the app's
   * *whole* preview set, and `astroliftPreviewEnvironmentsPage` exposes
   * no aggregate. Summing the page in hand would make "$X/day across N
   * previews" quietly mean "across this page", so the summary stays on
   * the flat field while the table below pages on the server.
   */
  const summary = useQuery<PreviewsResp>(LIST_PREVIEW_ENVIRONMENTS, {
    variables: { appSlug: slug },
    pollInterval: 30000,
    fetchPolicy: "cache-and-network",
  });

  // `astroliftPreviewEnvironmentsPage` filters on `appSlug` and searches
  // the app, branch, hostname, commit **and status**. It has no status
  // filter and no sort argument, so this table declares neither — the
  // status pills that used to filter one fetched page are gone (typing
  // `running` / `failed` / `torn_down` in the box is the server-side
  // equivalent).
  const table = useCursorTable<AstroliftPreviewEnvironment>({
    query: LIST_PREVIEW_ENVIRONMENTS_PAGE,
    variables: { appSlug: slug },
    extract: (d) => (d as PreviewsPageResp | undefined)?.astroliftPreviewEnvironmentsPage,
    searchVariable: "search",
    urlKey: "pv",
    pollInterval: 30000,
  });

  const [tearDown, tearState] = useMutation<{
    tearDownPreview: MutationResultLite;
  }>(TEAR_DOWN_PREVIEW, { refetchQueries: refetchFor(slug) });

  const [extendTtl, extendState] = useMutation<{
    extendPreviewTtl: MutationResultLite;
  }>(EXTEND_PREVIEW_TTL, { refetchQueries: refetchFor(slug) });

  // #659 — manual preview spin-up. Operators provision a preview from
  // any branch without opening a PR; the backend uses the same workflow
  // as the auto-preview path so TTL + resource limits are identical.
  // Gated client-side on `app.deploy`; the resolver also enforces.
  const [createPreview, createState] = useMutation<{
    createPreviewEnvironment: MutationResultLite;
  }>(CREATE_PREVIEW_ENVIRONMENT, { refetchQueries: refetchFor(slug) });

  const a = app.data?.astroliftApp ?? null;
  const list = React.useMemo(
    () => summary.data?.astroliftPreviewEnvironments ?? [],
    [summary.data?.astroliftPreviewEnvironments]
  );

  const counts = React.useMemo(
    () => ({
      running: list.filter((p) => p.status === "running").length,
      failed: list.filter((p) => p.status === "failed").length,
      tornDown: list.filter((p) => p.status === "torn_down").length,
    }),
    [list]
  );

  // #660 — monthly preview-spend roll-up. estimatedDailyCostUsd is
  // null for previews the cost driver can't price (e.g. clusters
  // without a billing plugin), so we sum only what's available and
  // surface the count of unpriced rows next to the dollar figure.
  const spend = React.useMemo(() => {
    const live = list.filter((p) => p.status !== "torn_down");
    let dailySum = 0;
    let priced = 0;
    let unpriced = 0;
    // A roll-up inherits the weakest estimate in it. Some GCP variants
    // total by summing every SKU in a service, an over-count by
    // construction, so a projection built partly from those is one too
    // and has to say so (#1509).
    let approximate = 0;
    for (const p of live) {
      if (typeof p.estimatedDailyCostUsd === "number") {
        dailySum += p.estimatedDailyCostUsd;
        priced += 1;
        if (p.estimatedCostApproximate) approximate += 1;
      } else {
        unpriced += 1;
      }
    }
    const today = new Date();
    const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
    return {
      dailyTotal: dailySum,
      monthlyProjection: dailySum * daysInMonth,
      priced,
      unpriced,
      approximate,
      liveCount: live.length,
    };
  }, [list]);

  const stale = list.filter((p) => p.status !== "torn_down").filter(isStale);

  const onExtend = React.useCallback(
    async (p: AstroliftPreviewEnvironment, days: PreviewTtlExtendDays) => {
      const { data } = await extendTtl({
        variables: { input: { id: p.id, days } },
      });
      const r = data?.extendPreviewTtl;
      if (r?.ok) {
        toast.success(t("toasts.ttlExtended", { pr: p.prNumber, days }));
      } else {
        toast.error(r?.errors[0]?.message ?? t("toasts.extendFailed"));
      }
    },
    [extendTtl, t]
  );

  /** Throws on failure so the confirm dialog stays open with the message. */
  async function onTearDown(p: AstroliftPreviewEnvironment) {
    const { data } = await tearDown({ variables: { input: { id: p.id } } });
    const r = data?.tearDownPreview;
    if (r?.ok) {
      toast.success(t("toasts.teardownEnqueued", { pr: p.prNumber }));
    } else {
      throw new Error(r?.errors[0]?.message ?? t("toasts.teardownFailed"));
    }
  }

  /** Resolves true when the preview is provisioning (close the sheet). */
  async function onCreate(branch: string): Promise<boolean> {
    if (!a) return false;
    const { data } = await createPreview({
      variables: { input: { appSlug: a.slug, branch } },
    });
    const r = data?.createPreviewEnvironment;
    if (r?.ok) {
      toast.success(`Preview for "${branch}" is provisioning`);
      return true;
    }
    toast.error(r?.errors[0]?.message ?? "Failed to create preview");
    return false;
  }

  return {
    slug,
    app: a,
    loading: app.loading,
    list,
    counts,
    spend,
    stale,
    table,
    tearingDown: tearState.loading,
    extending: extendState.loading,
    creating: createState.loading,
    onExtend,
    onTearDown,
    onCreate,
  };
}
