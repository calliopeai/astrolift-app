"use client";

import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useRouter } from "next/navigation";
import * as React from "react";

import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";

import { useClusterActions } from "./use-cluster-actions";

interface Resp {
  astroliftCluster: AstroliftTenantCluster | null;
}

const POLL_INTERVAL_MS = 4000;

/**
 * One cluster by slug (#2150), polled while its management workflow is in
 * flight, and the lifecycle actions its header offers. The data half of
 * ClusterDetail.
 */
export function useClusterDetail(slug: string) {
  // cache-and-network ensures a live fetch with the correct
  // X-Astrolift-Organization header on first client mount. The default
  // cache-first policy reads the stale SSR result (no org header) and
  // leaves this page showing "not found" while the breadcrumb already
  // reflects the cluster slug from the URL.
  const { data, loading, startPolling, stopPolling } = useQuery<Resp>(GET_CLUSTER, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });
  const cluster = data?.astroliftCluster ?? undefined;
  const lifecycle = cluster?.lifecycle ?? "registered";

  React.useEffect(() => {
    if (lifecycle === "managing") {
      startPolling(POLL_INTERVAL_MS);
      return () => stopPolling();
    }
    stopPolling();
    return undefined;
  }, [lifecycle, startPolling, stopPolling]);

  const router = useRouter();
  const t = useTranslations("clusters.unregister");
  const routeLease = React.useMemo(() => ({ slug }), [slug]);
  const currentRoute = React.useRef<{ lease: typeof routeLease; id: string | undefined } | null>({
    lease: routeLease,
    id: cluster?.id,
  });
  React.useLayoutEffect(() => {
    currentRoute.current = { lease: routeLease, id: cluster?.id };
    return () => {
      currentRoute.current = null;
    };
  }, [routeLease, cluster?.id]);
  const actions = useClusterActions();
  // The cluster is gone once unregistered; land on the list it left.
  async function onUnregister(c: AstroliftTenantCluster) {
    if (currentRoute.current?.lease !== routeLease || currentRoute.current.id !== c.id)
      throw new Error(t("sourceChanged"));
    await actions.onUnregister(c);
    if (
      currentRoute.current?.lease !== routeLease ||
      (currentRoute.current.id && currentRoute.current.id !== c.id)
    )
      return;
    try {
      router.push("/clusters");
    } catch {
      toast.warning(t("navigationWarning"));
    }
  }

  return { slug, cluster, loading, ...actions, onUnregister };
}
