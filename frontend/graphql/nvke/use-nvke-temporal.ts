"use client";
import { useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import type { DocumentNode } from "@apollo/client";
import type {
  NvkeTemporalAuthority,
  NvkeTemporalPanelProps,
  NvkeTemporalAction,
  NvkeTemporalValues,
} from "@/components/screens/nvke/NvkeTemporalPanel";
import type { NvkeSiteView } from "@/components/screens/nvke/types";
import type {
  ConfigureNvkeTemporalAuthorityInput,
  GetNvkeTemporalQuery,
} from "@/graphql/__generated__/operations";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import { GET_NVKE_TEMPORAL } from "./nvke.queries";
import {
  CONFIGURE_NVKE_TEMPORAL,
  VERIFY_NVKE_TEMPORAL,
  REMOVE_NVKE_TEMPORAL,
  SET_NVKE_TEMPORAL_ENABLED,
} from "./nvke.mutations";

export const useNvkeTemporal = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  enabled: boolean;
  onSiteRefresh: () => void;
}): NvkeTemporalPanelProps | null => {
  const { context, site } = args,
    client = useApolloClient(),
    t = useTranslations("nvke");
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const eligible =
    args.enabled &&
    !!site &&
    !!args.scopeKey &&
    !!context.scope &&
    !context.failed &&
    !context.loading &&
    context.access.read;
  const { key, isCurrent: current } = useNvkeEpoch(
    eligible ? JSON.stringify([args.scopeKey, site.id, site.version, refreshEpoch]) : ""
  );
  const read = useNvkeRead<GetNvkeTemporalQuery>(
    GET_NVKE_TEMPORAL,
    { siteId: site?.id },
    eligible ? key : "",
    0,
    context.scope?.organizationId
  );
  const data = read.data?.astroliftNvkeTemporalAuthority;
  const present = !!read.data && Object.hasOwn(read.data, "astroliftNvkeTemporalAuthority");
  const matched =
    data === null ||
    (!!data &&
      !!site &&
      data.siteId === site.id &&
      data.siteVersion === site.version &&
      validNvkeGuid(data.id) &&
      data.version > 0 &&
      data.deletedAt === null);
  const ready = eligible && !read.loading && !read.failed && present && matched;
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    error: boolean;
  } | null>(null);
  const busy = useRef(false);
  const pending = mutation?.key === key && mutation.pending;
  const failed = read.failed || (!read.loading && (!present || !matched));
  const uncertain = mutation?.key === key && mutation.error;
  const refresh = () => {
    setRefreshEpoch((value) => value + 1);
    setMutation(null);
    read.refresh();
    args.onSiteRefresh();
  };
  const run = async (
    document: DocumentNode,
    field: string,
    input: object,
    action: NvkeTemporalAction | "configure",
    values?: NvkeTemporalValues
  ) => {
    if (
      !current() ||
      !ready ||
      !site ||
      !context.scope ||
      !context.access.admin ||
      context.access.loading ||
      busy.current ||
      uncertain
    )
      return false;
    const version = data?.version ?? 0;
    busy.current = true;
    setMutation({ key, pending: true, error: false });
    try {
      const response = await client.mutate<
        Record<string, { ok: boolean; errors?: unknown[]; data?: NvkeTemporalAuthority | null }>
      >({
        mutation: document,
        variables: { input },
        fetchPolicy: "no-cache",
        context: {
          ...NVKE_REQUEST_CONTEXT,
          expectedNvkeOrganization: context.scope.organizationId,
        },
      });
      if (!current()) return false;
      const envelope = response.data?.[field],
        row = envelope?.data;
      if (
        !envelope?.ok ||
        envelope.errors?.length ||
        !row ||
        !validNvkeGuid(row.id) ||
        row.siteId !== site.id ||
        (data && row.id !== data.id)
      )
        throw new Error("UNCONFIRMED");
      if (action === "verify") {
        if (
          !data ||
          row.version !== version ||
          row.siteVersion !== site.version ||
          !row.isEnabled ||
          row.deletedAt ||
          row.verificationCode !== "NAMESPACE_VERIFIED" ||
          !row.verifiedAt
        )
          throw new Error("UNCONFIRMED");
      } else {
        if (
          row.version <= version ||
          row.siteVersion <= site.version ||
          Boolean(row.deletedAt) !== (action === "remove") ||
          row.verifiedAt ||
          row.verificationCode ||
          row.isEnabled !== (action === "configure" || action === "enable")
        )
          throw new Error("UNCONFIRMED");
        if (
          values &&
          (row.namespace !== values.namespace ||
            row.taskQueue !== values.taskQueue ||
            row.useTls !== values.useTls ||
            row.timeoutSeconds !== values.timeoutSeconds ||
            (!row.useTls && row.hasCredentials))
        )
          throw new Error("UNCONFIRMED");
        if (values && values.credentialMode !== "preserve") {
          const has =
            values.credentialMode === "replace" &&
            Boolean(
              values.apiKey || values.caPem || values.clientCertificatePem || values.clientKeyPem
            );
          if (row.hasCredentials !== has) throw new Error("UNCONFIRMED");
        }
      }
      setMutation({ key, pending: false, error: false });
      refresh();
      return true;
    } catch {
      if (current()) setMutation({ key, pending: false, error: true });
      return false;
    } finally {
      busy.current = false;
    }
  };
  if (!eligible || !site) return null;
  return {
    site,
    authority:
      ready && data
        ? { ...data, verifiedAt: data.verifiedAt ?? null, deletedAt: data.deletedAt ?? null }
        : null,
    loading: read.loading,
    error: failed ? t("readFailed") : uncertain ? t("configurationFailed") : null,
    canManage: context.access.admin && !context.access.loading && ready,
    pending,
    onRefresh: refresh,
    onConfigure: (values) => {
      const credentials =
        values.credentialMode === "preserve"
          ? null
          : values.credentialMode === "clear"
            ? { apiKey: "", caPem: "", clientCertificatePem: "", clientKeyPem: "" }
            : {
                apiKey: values.apiKey,
                caPem: values.caPem,
                clientCertificatePem: values.clientCertificatePem,
                clientKeyPem: values.clientKeyPem,
              };
      const input: ConfigureNvkeTemporalAuthorityInput = {
        siteId: site.id,
        expectedSiteVersion: site.version,
        expectedVersion: data?.version ?? 0,
        expectedActorId: context.scope!.actorId,
        address: values.address,
        namespace: values.namespace,
        taskQueue: values.taskQueue,
        serverName: values.serverName,
        useTls: values.useTls,
        timeoutSeconds: values.timeoutSeconds,
        credentials,
      };
      return run(
        CONFIGURE_NVKE_TEMPORAL,
        "configureNvkeTemporalAuthority",
        input,
        "configure",
        values
      );
    },
    onAction: (action) => {
      if (!data) return Promise.resolve(false);
      const input = {
        siteId: site.id,
        expectedSiteVersion: site.version,
        expectedVersion: data.version,
        expectedActorId: context.scope!.actorId,
      };
      if (action === "verify")
        return run(VERIFY_NVKE_TEMPORAL, "verifyNvkeTemporalAuthority", input, action);
      if (action === "remove")
        return run(REMOVE_NVKE_TEMPORAL, "removeNvkeTemporalAuthority", input, action);
      return run(
        SET_NVKE_TEMPORAL_ENABLED,
        "setNvkeTemporalAuthorityEnabled",
        { ...input, enabled: action === "enable" },
        action
      );
    },
  };
};
