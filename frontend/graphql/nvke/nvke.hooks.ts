"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { useApolloClient } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { GET_MY_PERMISSIONS } from "@/graphql/permissions/astrolift.queries";
import { permissionMatches } from "@/lib/permissions/astrolift-permissions";
import { useScopedCursorTable } from "@/components/data-table/use-scoped-cursor-table";
import type {
  NvkeAccess,
  NvkeSiteView,
  NvkeSitesScreenProps,
} from "@/components/screens/nvke/types";
import { validNvkeGuid, nvkeScopeKey, type NvkeBrowserScope } from "@/lib/nvke/recovery";
import type {
  CreateNvkeSiteMutation,
  CreateNvkeSiteMutationVariables,
  GetNvkeIdentityQuery,
  ListNvkeSitesQuery,
} from "@/graphql/__generated__/operations";
import { CREATE_NVKE_SITE } from "./nvke.mutations";
import { GET_NVKE_IDENTITY, LIST_NVKE_SITES } from "./nvke.queries";
import { NVKE_REQUEST_CONTEXT, useNvkeRead } from "./use-scoped-read";

const serverIdentity = () => {
  if (typeof window === "undefined") return "";
  const endpoint = process.env.NEXT_PUBLIC_API_ROOT
    ? `${process.env.NEXT_PUBLIC_API_ROOT}/app/gql/config/`
    : "/app/gql/config/";
  try {
    const url = new URL(endpoint, window.location.origin);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
      ? url.href
      : "";
  } catch {
    return "";
  }
};

export const useNvkeContext = () => {
  const org = useActiveOrg(),
    me = useMe();
  const server = serverIdentity();
  const base =
    server &&
    validNvkeGuid(org.org?.id) &&
    me.user?.id &&
    !org.loading &&
    !org.error &&
    !me.loading &&
    !me.error
      ? JSON.stringify([server, org.org.id, me.user.id])
      : "";
  const identity = useNvkeRead<GetNvkeIdentityQuery>(GET_NVKE_IDENTITY, {}, base, 0, org.org?.id);
  const permissions = useNvkeRead<{ astroliftMyPermissions: string[] }>(
    GET_MY_PERMISSIONS,
    {},
    base,
    0,
    org.org?.id
  );
  const actorId = identity.data?.astroliftMyProfile?.userId;
  const scope: NvkeBrowserScope | null =
    base && Number.isSafeInteger(actorId) && actorId! > 0 && org.org
      ? { server, organizationId: org.org.id, actorId: actorId! }
      : null;
  const loading = org.loading || me.loading || identity.loading || permissions.loading;
  const failed =
    !!org.error ||
    !!me.error ||
    identity.failed ||
    permissions.failed ||
    (!loading && (!scope || !permissions.data));
  const granted = new Set(permissions.data?.astroliftMyPermissions ?? []);
  const access: NvkeAccess = {
    read: !failed && !loading && permissionMatches(granted, "nvke.read"),
    manage: !failed && !loading && permissionMatches(granted, "nvke.manage"),
    admin: !failed && !loading && permissionMatches(granted, "nvke.admin"),
    loading,
  };
  return {
    scope,
    scopeKey: scope && !failed && !loading ? nvkeScopeKey(scope) : "",
    access,
    loading,
    failed,
  };
};

/** Capture one mounted visit, so A → B → A cannot accept A's earlier completion. */
export const useNvkeEpoch = (scopeKey: string) => {
  const [visit, setVisit] = useState({ key: scopeKey, generation: 0 });
  if (visit.key !== scopeKey) setVisit({ key: scopeKey, generation: visit.generation + 1 });
  const captured = JSON.stringify([scopeKey, visit.generation]);
  const latest = useRef(captured);
  useLayoutEffect(() => {
    latest.current = captured;
    return () => {
      latest.current = "";
    };
  }, [captured]);
  return { isCurrent: () => latest.current === captured, key: captured };
};

export const useNvkeSites = (): NvkeSitesScreenProps & { scopeKey: string } => {
  const context = useNvkeContext(),
    client = useApolloClient(),
    router = useRouter();
  const t = useTranslations("nvke");
  const { isCurrent: current, key: visitKey } = useNvkeEpoch(context.scopeKey);
  const busy = useRef(false);
  const [mutation, setMutation] = useState<{
    key: string;
    pending: boolean;
    failed: boolean;
  } | null>(null);
  const table = useScopedCursorTable<NvkeSiteView>(
    {
      query: LIST_NVKE_SITES,
      extract: (data) => (data as ListNvkeSitesQuery | undefined)?.astroliftNvkeSites,
      skip: !context.scopeKey || !context.access.read,
      urlKey: "nvke-sites",
    },
    context.scopeKey,
    { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: context.scope?.organizationId }
  );
  return {
    scopeKey: visitKey,
    table,
    access: context.access,
    pending: mutation?.key === visitKey && mutation.pending,
    error: context.failed
      ? t("readFailed")
      : mutation?.key === visitKey && mutation.failed
        ? t("requestFailed")
        : null,
    onCreate: async (values) => {
      if (!current() || busy.current || !context.scopeKey || !context.access.admin) return false;
      busy.current = true;
      setMutation({ key: visitKey, pending: true, failed: false });
      try {
        const response = await client.mutate<
          CreateNvkeSiteMutation,
          CreateNvkeSiteMutationVariables
        >({
          mutation: CREATE_NVKE_SITE,
          variables: { input: values },
          fetchPolicy: "no-cache",
          context: {
            ...NVKE_REQUEST_CONTEXT,
            expectedNvkeOrganization: context.scope!.organizationId,
          },
        });
        if (!current()) return false;
        const result = response.data?.createNvkeSite;
        if (!result?.ok || !validNvkeGuid(result.data?.id)) throw new Error("REQUEST_FAILED");
        setMutation({ key: visitKey, pending: false, failed: false });
        router.push(`/nvke/${result.data.id}`);
        return true;
      } catch {
        if (current()) setMutation({ key: visitKey, pending: false, failed: true });
        return false;
      } finally {
        busy.current = false;
      }
    },
  };
};
