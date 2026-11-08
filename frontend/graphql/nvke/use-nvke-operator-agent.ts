"use client";
import { useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import type { DocumentNode } from "@apollo/client";
import type {
  NvkeOperatorAgentAuthority,
  NvkeOperatorAgentPanelProps,
  NvkeOperatorAgentAction,
  NvkeOperatorAgentValues,
} from "@/components/screens/nvke/NvkeOperatorAgentPanel";
import type { NvkeSiteView } from "@/components/screens/nvke/types";
import type {
  ConfigureNvkeOperatorAuthorityInput,
  GetNvkeOperatorAuthorityQuery,
} from "@/graphql/__generated__/operations";
import { operatorAgentAuthoritySchema } from "@/components/screens/nvke/schema";
import { validNvkeGuid } from "@/lib/nvke/recovery";
import { useNvkeEpoch, type useNvkeContext } from "./nvke.hooks";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";
import { GET_NVKE_OPERATOR_AUTHORITY } from "./nvke-operator.queries";
import {
  CONFIGURE_NVKE_OPERATOR_AUTHORITY,
  VERIFY_NVKE_OPERATOR_AUTHORITY,
  REMOVE_NVKE_OPERATOR_AUTHORITY,
  SET_NVKE_OPERATOR_AUTHORITY_ENABLED,
} from "./nvke-operator.mutations";

const validMetadata = (value: unknown): value is NvkeOperatorAgentAuthority => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return (
    operatorAgentAuthoritySchema.safeParse({
      ...row,
      credentialMode: "preserve",
      caPem: "",
      clientCertificatePem: "",
      clientKeyPem: "",
    }).success &&
    validNvkeGuid(row.id) &&
    validNvkeGuid(row.siteId) &&
    Number.isSafeInteger(row.version) &&
    Number(row.version) > 0 &&
    Number.isSafeInteger(row.siteVersion) &&
    Number(row.siteVersion) > 0 &&
    typeof row.isEnabled === "boolean" &&
    row.hasCredentials === true &&
    (row.verifiedAt === null ||
      (typeof row.verifiedAt === "string" && Number.isFinite(Date.parse(row.verifiedAt)))) &&
    (row.deletedAt === null ||
      (typeof row.deletedAt === "string" && Number.isFinite(Date.parse(row.deletedAt)))) &&
    typeof row.verificationCode === "string"
  );
};

export const useNvkeOperatorAgent = (args: {
  context: ReturnType<typeof useNvkeContext>;
  scopeKey: string;
  site: NvkeSiteView | null;
  enabled: boolean;
  onSiteRefresh: () => void;
}): NvkeOperatorAgentPanelProps | null => {
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
    !context.access.loading &&
    context.access.read;
  const { key, isCurrent: current } = useNvkeEpoch(
    eligible
      ? JSON.stringify([
          args.scopeKey,
          context.scope,
          context.access.admin,
          context.access.loading,
          site.id,
          site.version,
          refreshEpoch,
        ])
      : ""
  );
  const read = useNvkeRead<GetNvkeOperatorAuthorityQuery>(
    GET_NVKE_OPERATOR_AUTHORITY,
    { siteId: site?.id },
    eligible ? key : "",
    0,
    context.scope?.organizationId
  );
  const data = read.data?.astroliftNvkeOperatorAuthority;
  const present = !!read.data && Object.hasOwn(read.data, "astroliftNvkeOperatorAuthority");
  const matched =
    data === null ||
    (!!data &&
      !!site &&
      data.siteId === site.id &&
      data.siteVersion === site.version &&
      validNvkeGuid(data.id) &&
      Number.isSafeInteger(data.version) &&
      data.version > 0 &&
      validMetadata(data) &&
      data.deletedAt === null);
  const ready = eligible && !read.loading && !read.failed && present && matched;
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    error: boolean;
  } | null>(null);
  const busy = useRef(false);
  const blockedEpoch = useRef("");
  const pending = mutation?.key === key && mutation.pending;
  const failed = read.failed || (!read.loading && (!present || !matched));
  const uncertain = mutation?.key === key && mutation.error;
  const refresh = () => {
    if (!current()) return;
    setRefreshEpoch((value) => value + 1);
    setMutation(null);
    read.refresh();
    args.onSiteRefresh();
  };
  const run = async (
    document: DocumentNode,
    field: string,
    input: object,
    action: NvkeOperatorAgentAction | "configure",
    values?: NvkeOperatorAgentValues
  ) => {
    if (
      !current() ||
      !ready ||
      !site ||
      !context.scope ||
      !context.access.admin ||
      context.access.loading ||
      busy.current ||
      blockedEpoch.current === key ||
      uncertain
    )
      return false;
    const version = data?.version ?? 0;
    busy.current = true;
    setMutation({ key, pending: true, error: false });
    try {
      const response = await client.mutate<
        Record<
          string,
          { ok: boolean; errors?: unknown[]; data?: NvkeOperatorAgentAuthority | null }
        >
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
        !validMetadata(row) ||
        row.siteId !== site.id ||
        (data && row.id !== data.id)
      )
        throw new Error("UNCONFIRMED");
      if (
        !values &&
        data &&
        (row.endpoint !== data.endpoint ||
          row.instanceId !== data.instanceId ||
          row.bundleManifestSha256 !== data.bundleManifestSha256 ||
          row.timeoutSeconds !== data.timeoutSeconds)
      )
        throw new Error("UNCONFIRMED");
      if (action === "verify") {
        if (
          !data ||
          row.version !== version ||
          row.siteVersion !== site.version ||
          !row.isEnabled ||
          row.deletedAt ||
          row.verificationCode !== "AGENT_VERIFIED" ||
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
          (row.endpoint !== values.endpoint.replace(/\/+$/, "") ||
            row.instanceId !== values.instanceId ||
            row.bundleManifestSha256 !== values.bundleManifestSha256 ||
            row.timeoutSeconds !== values.timeoutSeconds ||
            !row.hasCredentials)
        )
          throw new Error("UNCONFIRMED");
      }
      setMutation({ key, pending: false, error: false });
      refresh();
      return true;
    } catch {
      if (current()) {
        blockedEpoch.current = key;
        setMutation({ key, pending: false, error: true });
      }
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
        ? {
            id: data.id,
            version: data.version,
            siteId: data.siteId,
            siteVersion: data.siteVersion,
            endpoint: data.endpoint,
            instanceId: data.instanceId,
            bundleManifestSha256: data.bundleManifestSha256,
            isEnabled: data.isEnabled,
            timeoutSeconds: data.timeoutSeconds,
            hasCredentials: data.hasCredentials,
            verifiedAt: data.verifiedAt ?? null,
            verificationCode: data.verificationCode,
            deletedAt: data.deletedAt ?? null,
          }
        : null,
    loading: read.loading,
    error: failed ? t("readFailed") : uncertain ? t("configurationFailed") : null,
    canManage: context.access.admin && !context.access.loading && ready,
    pending,
    onRefresh: refresh,
    onConfigure: (values) => {
      if (
        !operatorAgentAuthoritySchema.safeParse(values).success ||
        (!data && values.credentialMode !== "replace")
      )
        return Promise.resolve(false);
      const credentials =
        values.credentialMode === "preserve"
          ? null
          : {
              caPem: values.caPem,
              clientCertificatePem: values.clientCertificatePem,
              clientKeyPem: values.clientKeyPem,
            };
      const input: ConfigureNvkeOperatorAuthorityInput = {
        siteId: site.id,
        expectedSiteVersion: site.version,
        expectedVersion: data?.version ?? 0,
        expectedActorId: context.scope!.actorId,
        endpoint: values.endpoint,
        instanceId: values.instanceId,
        bundleManifestSha256: values.bundleManifestSha256,
        timeoutSeconds: values.timeoutSeconds,
        credentials,
      };
      return run(
        CONFIGURE_NVKE_OPERATOR_AUTHORITY,
        "configureNvkeOperatorAuthority",
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
        return run(VERIFY_NVKE_OPERATOR_AUTHORITY, "verifyNvkeOperatorAuthority", input, action);
      if (action === "remove")
        return run(REMOVE_NVKE_OPERATOR_AUTHORITY, "removeNvkeOperatorAuthority", input, action);
      return run(
        SET_NVKE_OPERATOR_AUTHORITY_ENABLED,
        "setNvkeOperatorAuthorityEnabled",
        { ...input, enabled: action === "enable" },
        action
      );
    },
  };
};
