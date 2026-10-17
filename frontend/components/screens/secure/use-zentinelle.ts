"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { usePathname, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { useCursorFeed } from "@/components/feed/use-cursor-feed";
import type {
  ZentinelleAuditPageQuery,
  ZentinelleCapabilityQuery,
  ZentinelleClusterChoicesQuery,
  ZentinelleConnectionQuery,
  ZentinellePoliciesQuery,
  ZentinelleScopeOptionsQuery,
  ZentinelleUsageQuery,
} from "@/graphql/__generated__/operations";
import {
  CONNECT_ZENTINELLE,
  DISCONNECT_ZENTINELLE,
  EXPORT_ZENTINELLE_AUDIT,
  REGISTER_ZENTINELLE_CLUSTER,
  ROTATE_ZENTINELLE_GATEWAY_CREDENTIAL,
  SET_ZENTINELLE_GATEWAY_ENABLED,
  SET_ZENTINELLE_GATEWAY_URL,
  UNREGISTER_ZENTINELLE_CLUSTER,
} from "@/graphql/zentinelle/zentinelle.mutations";
import {
  ZENTINELLE_AUDIT_PAGE,
  ZENTINELLE_CAPABILITY,
  ZENTINELLE_CLUSTER_CHOICES,
  ZENTINELLE_CONNECTION,
  ZENTINELLE_POLICIES,
  ZENTINELLE_SCOPE_OPTIONS,
  ZENTINELLE_USAGE,
} from "@/graphql/zentinelle/zentinelle.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { ZentinelleExportFormat } from "./ZentinelleAudit";
import type { ZentinelleScreenProps } from "./ZentinelleScreen";
import {
  ALL_SCOPE,
  hasAnyAccess,
  scopeVariables,
  usageTotals,
  windowStart,
  ZENTINELLE_TABS,
  zentinelleAccess,
  type ZentinelleScope,
  type ZentinelleTab,
  type ZentinelleWindow,
} from "./zentinelle-model";

interface Payload {
  ok: boolean;
  errors: { code: string; message: string }[];
}

/** Codes another surface already answers: the step-up prompt and the approval toast. */
const HANDLED = new Set(["STEP_UP_REQUIRED", "APPROVAL_REQUIRED"]);

const REFETCH = ["ZentinelleConnection", "ZentinelleCapability"];

/** A write's reason to show, or null when it worked or another prompt took it. */
async function outcome(
  run: () => Promise<{ data?: unknown }>,
  field: string,
  fallback: string
): Promise<string | null> {
  try {
    const { data } = await run();
    const payload = (data as Record<string, Payload> | undefined)?.[field];
    if (payload?.ok) return null;
    const error = payload?.errors?.[0];
    if (error && HANDLED.has(error.code)) return null;
    return error?.message ?? fallback;
  } catch (err) {
    return err instanceof Error ? err.message : fallback;
  }
}

function download(filename: string, content: string, contentType: string) {
  const url = URL.createObjectURL(new Blob([content], { type: contentType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** The data half of ZentinelleScreen: permissions, the connection, and each tab's read. */
export function useZentinelle(): ZentinelleScreenProps {
  const t = useTranslations("zentinelleScreen");
  const params = useSearchParams();
  const pathname = usePathname();
  const requested = params.get("tab");
  const tab: ZentinelleTab = ZENTINELLE_TABS.includes(requested as ZentinelleTab)
    ? (requested as ZentinelleTab)
    : "overview";

  const perms = useMyPermissions();
  const access = zentinelleAccess(perms.can);
  const anyAccess = hasAnyAccess(access);

  const capability = useQuery<ZentinelleCapabilityQuery>(ZENTINELLE_CAPABILITY, {
    fetchPolicy: "cache-and-network",
    skip: !anyAccess,
  });
  const conn = useQuery<ZentinelleConnectionQuery>(ZENTINELLE_CONNECTION, {
    fetchPolicy: "cache-and-network",
    skip: !access.status,
  });
  const connection = conn.data?.astroliftZentinelleConnection ?? null;
  const connected = access.status
    ? connection?.status === "connected"
    : Boolean(capability.data?.astroliftZentinelleCapability.connected);
  const loading =
    (perms.loading && perms.granted.size === 0) ||
    (access.status ? conn.loading && !conn.data : capability.loading && !capability.data);

  const live = connected && !loading;
  const [now] = React.useState(() => new Date());
  const [range, setRange] = React.useState<ZentinelleWindow>("7d");
  const [usageScope, setUsageScope] = React.useState<ZentinelleScope>(ALL_SCOPE);
  const [policyScope, setPolicyScope] = React.useState<ZentinelleScope>(ALL_SCOPE);
  const [auditScope, setAuditScope] = React.useState<ZentinelleScope>(ALL_SCOPE);
  const [clusterSearch, setClusterSearch] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [busyClusterId, setBusyClusterId] = React.useState<string | null>(null);
  const [exporting, setExporting] = React.useState(false);

  // ---- overview health -----------------------------------------------------
  const healthSkip = !live || tab !== "overview" || !access.usage;
  const day = useQuery<ZentinelleUsageQuery>(ZENTINELLE_USAGE, {
    variables: { since: windowStart("24h", now) },
    fetchPolicy: "cache-and-network",
    skip: healthSkip,
  });
  const month = useQuery<ZentinelleUsageQuery>(ZENTINELLE_USAGE, {
    variables: { since: windowStart("month", now) },
    fetchPolicy: "cache-and-network",
    skip: healthSkip,
  });
  const healthError = day.error?.message ?? month.error?.message ?? null;

  // ---- usage, policies, audit --------------------------------------------
  const scopeOptions = useQuery<ZentinelleScopeOptionsQuery>(ZENTINELLE_SCOPE_OPTIONS, {
    fetchPolicy: "cache-first",
    errorPolicy: "all",
    skip: !live || !["usage", "policies", "audit"].includes(tab),
  });
  const options = {
    projects: scopeOptions.data?.astroliftProjectsPage?.items ?? [],
    teams: scopeOptions.data?.astroliftTeamsPage?.items ?? [],
  };

  const usage = useQuery<ZentinelleUsageQuery>(ZENTINELLE_USAGE, {
    variables: { since: windowStart(range, now), ...scopeVariables(usageScope) },
    fetchPolicy: "cache-and-network",
    skip: !live || tab !== "usage" || !access.usage,
  });
  const policies = useQuery<ZentinellePoliciesQuery>(ZENTINELLE_POLICIES, {
    variables: {
      agentSlug: scopeVariables(policyScope).agentSlug,
      projectId: scopeVariables(policyScope).projectId,
    },
    fetchPolicy: "cache-and-network",
    skip: !live || tab !== "policies" || !access.policies,
  });
  const auditSince = windowStart(range, now);
  const audit = useCursorFeed<
    ZentinelleAuditPageQuery,
    ZentinelleAuditPageQuery["astroliftZentinelleAuditPage"]["events"][number]
  >(ZENTINELLE_AUDIT_PAGE, {
    variables: { since: auditSince, ...scopeVariables(auditScope) },
    select: (d) =>
      d && {
        items: d.astroliftZentinelleAuditPage.events,
        nextCursor: d.astroliftZentinelleAuditPage.nextCursor,
      },
    keyOf: (e) => e.id,
    cursorVar: "cursor",
    pageSize: 50,
    live: false,
    skip: !live || tab !== "audit" || !access.audit,
  });

  // ---- clusters ------------------------------------------------------------
  const choices = useQuery<ZentinelleClusterChoicesQuery>(ZENTINELLE_CLUSTER_CHOICES, {
    variables: { search: clusterSearch || null },
    fetchPolicy: "cache-and-network",
    skip: !live || tab !== "clusters" || !access.gatewayManage,
  });

  // ---- writes --------------------------------------------------------------
  const opts = { refetchQueries: REFETCH, awaitRefetchQueries: true };
  const [connectMutation, connecting] = useMutation(CONNECT_ZENTINELLE, opts);
  const [gatewayUrlMutation] = useMutation(SET_ZENTINELLE_GATEWAY_URL, opts);
  const [disconnectMutation] = useMutation(DISCONNECT_ZENTINELLE, opts);
  const [registerMutation] = useMutation(REGISTER_ZENTINELLE_CLUSTER, opts);
  const [enabledMutation] = useMutation(SET_ZENTINELLE_GATEWAY_ENABLED, opts);
  const [rotateMutation] = useMutation(ROTATE_ZENTINELLE_GATEWAY_CREDENTIAL, opts);
  const [unregisterMutation] = useMutation(UNREGISTER_ZENTINELLE_CLUSTER, opts);
  const [exportMutation] = useMutation(EXPORT_ZENTINELLE_AUDIT);
  const failed = t("writeFailed");

  async function onConnection(run: () => Promise<{ data?: unknown }>, field: string) {
    setBusy(true);
    try {
      return await outcome(run, field, failed);
    } finally {
      setBusy(false);
    }
  }

  async function onCluster(
    clusterId: string,
    run: () => Promise<{ data?: unknown }>,
    field: string
  ) {
    setBusyClusterId(clusterId);
    try {
      return await outcome(run, field, failed);
    } finally {
      setBusyClusterId(null);
    }
  }

  async function onExport(format: ZentinelleExportFormat) {
    setExporting(true);
    try {
      const { data } = await exportMutation({
        variables: { input: { since: auditSince, format, ...scopeVariables(auditScope) } },
      });
      const payload = (
        data as
          | {
              exportZentinelleAudit: Payload & {
                data: {
                  filename: string;
                  content: string;
                  contentType: string;
                  rowCount: number;
                } | null;
              };
            }
          | undefined
      )?.exportZentinelleAudit;
      if (payload?.ok && payload.data) {
        download(payload.data.filename, payload.data.content, payload.data.contentType);
        toast.success(t("audit.exported", { count: payload.data.rowCount }));
      } else if (!HANDLED.has(payload?.errors?.[0]?.code ?? "")) {
        toast.error(payload?.errors?.[0]?.message ?? failed);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : failed);
    } finally {
      setExporting(false);
    }
  }

  const input = (clusterId: string) => ({ variables: { input: { clusterId } } });

  return {
    loading,
    error: access.status && conn.error && !conn.data ? conn.error.message : null,
    onRetry: () => void conn.refetch(),
    access,
    connected,
    connection,
    tab,
    tabHref: (key) => (key === "overview" ? pathname : `${pathname}?tab=${key}`),
    connect: {
      connecting: connecting.loading,
      onConnect: (value) =>
        outcome(
          () => connectMutation({ variables: { input: value } }),
          "connectZentinelle",
          failed
        ),
    },
    manage: {
      busy,
      onSetGatewayUrl: (gatewayUrl) =>
        onConnection(
          () => gatewayUrlMutation({ variables: { input: { gatewayUrl } } }),
          "setZentinelleGatewayUrl"
        ),
      onDisconnect: async (force) => {
        const failure = await onConnection(
          () => disconnectMutation({ variables: { input: { force } } }),
          "disconnectZentinelle"
        );
        if (!failure) toast.success(t("connection.disconnected"));
        return failure;
      },
    },
    health: {
      usage: {
        data:
          access.usage && day.data && month.data
            ? {
                day: usageTotals(day.data.astroliftZentinelleUsage.rows),
                month: usageTotals(month.data.astroliftZentinelleUsage.rows),
              }
            : null,
        loading: !healthSkip && (day.loading || month.loading) && !(day.data && month.data),
        error: healthError,
        onRetry: () => {
          void day.refetch();
          void month.refetch();
        },
      },
    },
    clusters: {
      busyClusterId,
      choices: {
        items: choices.data?.astroliftClustersPage.items ?? [],
        loading: choices.loading && !choices.data,
        search: clusterSearch,
      },
      onSearch: setClusterSearch,
      onRegister: (id) =>
        onCluster(id, () => registerMutation(input(id)), "registerZentinelleCluster"),
      onSetEnabled: (id, enabled) =>
        onCluster(
          id,
          () => enabledMutation({ variables: { input: { clusterId: id, enabled } } }),
          "setZentinelleGatewayEnabled"
        ),
      onRotate: (id) =>
        onCluster(id, () => rotateMutation(input(id)), "rotateZentinelleGatewayCredential"),
      onUnregister: (id) =>
        onCluster(id, () => unregisterMutation(input(id)), "unregisterZentinelleCluster"),
    },
    usage: {
      filter: {
        scope: usageScope,
        onScopeChange: setUsageScope,
        options,
        range,
        onRangeChange: setRange,
      },
      usage: {
        data: usage.data?.astroliftZentinelleUsage.rows ?? [],
        loading: usage.loading && !usage.data,
        error: usage.error && !usage.data ? usage.error.message : null,
        onRetry: () => void usage.refetch(),
      },
    },
    policies: {
      filter: { scope: policyScope, onScopeChange: setPolicyScope, options },
      policies: {
        data: policies.data?.astroliftZentinellePolicies ?? [],
        loading: policies.loading && !policies.data,
        error: policies.error && !policies.data ? policies.error.message : null,
        onRetry: () => void policies.refetch(),
      },
    },
    audit: {
      filter: {
        scope: auditScope,
        onScopeChange: setAuditScope,
        options,
        range,
        onRangeChange: setRange,
      },
      feed: audit.feed,
      exporting,
      onExport: (format) => void onExport(format),
    },
  };
}
