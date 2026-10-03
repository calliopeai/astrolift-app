"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { CONNECT_HUGGING_FACE } from "@/graphql/models/hosting.mutations";
import {
  GET_MODEL_HOSTING_ACTION,
  GET_MODEL_SOURCE_ACCESS,
  LIST_HUGGING_FACE_CONNECTIONS,
} from "@/graphql/models/hosting.queries";
import type {
  ConnectHuggingFaceMutation,
  ConnectHuggingFaceMutationVariables,
  GetModelHostingActionQuery,
  GetModelHostingActionQueryVariables,
  GetModelSourceAccessQuery,
  GetModelSourceAccessQueryVariables,
  ListHuggingFaceConnectionsQuery,
  ListHuggingFaceConnectionsQueryVariables,
} from "@/graphql/__generated__/operations";
import type { HostingConnection, ModelHostingSourceProps } from "./ModelHostingSourcePanel";

export function useModelHostingSource(
  model: { repoId: string; revisionSha: string } | null,
  onManualSource: ModelHostingSourceProps["onManualSource"]
) {
  const t = useTranslations("models.shared.hosting");
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const { user, loading: actorLoading, error: actorError } = useMe();
  const organizationId = org?.id ?? "",
    actorId = user?.id ?? "";
  const identity = `${organizationId}:${actorId}`;
  const identityReady = Boolean(
    organizationId && actorId && !orgLoading && !orgError && !actorLoading && !actorError
  );
  const authority = useQuery<GetModelHostingActionQuery, GetModelHostingActionQueryVariables>(
    GET_MODEL_HOSTING_ACTION,
    {
      variables: { organizationId },
      skip: !identityReady,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const allowed =
    !identityReady || authority.loading
      ? null
      : authority.error
        ? false
        : (authority.data?.modelHostingAction.allowed ?? false);
  const authorityError =
    orgError?.message ??
    actorError?.message ??
    authority.error?.message ??
    (!organizationId || !actorId ? null : (authority.data?.modelHostingAction.reason ?? null));
  const [state, setState] = useState<{
    identity: string;
    page: number;
    selected: HostingConnection | null;
  }>({ identity, page: 1, selected: null });
  if (state.identity !== identity) setState({ identity, page: 1, selected: null });
  const current = state.identity === identity ? state : { identity, page: 1, selected: null };
  const connections = useQuery<
    ListHuggingFaceConnectionsQuery,
    ListHuggingFaceConnectionsQueryVariables
  >(LIST_HUGGING_FACE_CONNECTIONS, {
    variables: { organizationId, page: current.page, pageSize: 25 },
    skip: allowed !== true,
    fetchPolicy: "no-cache",
    context: { queryDeduplication: false },
  });
  const [connect] = useMutation<ConnectHuggingFaceMutation, ConnectHuggingFaceMutationVariables>(
    CONNECT_HUGGING_FACE,
    { fetchPolicy: "no-cache" }
  );
  const key = JSON.stringify([identity, identityReady, allowed, authorityError]);
  const [scope, setScope] = useState({ key, revision: 0 });
  if (scope.key !== key) setScope({ key, revision: scope.revision + 1 });
  const revision = scope.revision;
  const latest = useRef({ identity, revision, allowed });
  const mounted = useRef(false);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useLayoutEffect(() => {
    latest.current = { identity, revision, allowed };
  }, [identity, revision, allowed]);
  const active = () =>
    mounted.current &&
    latest.current.identity === identity &&
    latest.current.revision === revision &&
    latest.current.allowed === true;
  const sourceVariables = {
    organizationId,
    modelRepo: model?.repoId ?? "",
    revisionSha: model?.revisionSha ?? "",
    connectionId: current.selected?.id ?? null,
    expectedConnectionVersion: current.selected?.version ?? null,
  };
  const access = useQuery<GetModelSourceAccessQuery, GetModelSourceAccessQueryVariables>(
    GET_MODEL_SOURCE_ACCESS,
    {
      variables: sourceVariables,
      skip: allowed !== true || !model,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const snapshot =
    allowed === true && model && !access.loading && !access.error
      ? access.data?.clusterModelSourceAccess
      : null;
  const accessConfirmed = Boolean(
    snapshot?.accessible &&
    snapshot.model?.repoId === model?.repoId &&
    snapshot.model?.revisionSha === model?.revisionSha
  );
  const props: ModelHostingSourceProps = {
    scopeKey: `${identity}:${revision}`,
    allowed,
    authorityError,
    onRetryAuthority: () => {
      if (identityReady) void authority.refetch().catch(() => {});
    },
    connections: allowed === true ? (connections.data?.huggingFaceConnectionsPage.items ?? []) : [],
    selectedConnection: current.selected,
    connectionsLoading: allowed === true && connections.loading,
    connectionsError: allowed === true ? (connections.error?.message ?? null) : null,
    connectionPage: current.page,
    connectionPages: Math.max(
      1,
      Math.ceil((connections.data?.huggingFaceConnectionsPage.totalCount ?? 0) / 25)
    ),
    onConnectionPage: (page) => {
      if (active()) setState({ identity, page, selected: null });
    },
    onRetryConnections: () => {
      if (active()) void connections.refetch().catch(() => {});
    },
    onSelectConnection: (id) => {
      if (!active() || connections.loading || connections.error) return;
      const selected = id
        ? connections.data?.huggingFaceConnectionsPage.items.find((row) => row.id === id)
        : null;
      if (!id || selected) setState({ identity, page: current.page, selected: selected ?? null });
    },
    onConnect: async (name, token) => {
      if (!active()) return { accepted: false, message: t("changed") };
      try {
        const result = (await connect({ variables: { input: { organizationId, name, token } } }))
          .data?.connectHuggingFace;
        if (!result?.ok)
          return { accepted: false, message: result?.errors[0]?.message ?? t("connectFailed") };
        if (!result.data)
          return { accepted: true, account: null, current: active(), refreshFailed: true };
        const isCurrent = active();
        if (!isCurrent)
          return {
            accepted: true,
            account: result.data.accountUsername,
            current: false,
            refreshFailed: false,
          };
        setState({ identity, page: current.page, selected: result.data });
        let refreshFailed = false;
        try {
          await connections.refetch();
        } catch {
          refreshFailed = true;
        }
        return {
          accepted: true,
          account: result.data.accountUsername,
          current: active(),
          refreshFailed,
        };
      } catch {
        return { accepted: false, message: t("connectFailed") };
      }
    },
    onManualSource: (candidate) => {
      if (active()) onManualSource(candidate);
    },
  };
  return {
    props,
    actorId,
    identityReady,
    connection: current.selected
      ? { connectionId: current.selected.id, expectedConnectionVersion: current.selected.version }
      : null,
    access: {
      confirmed: accessConfirmed,
      loading: Boolean(model && allowed === true && access.loading),
      reason: access.error?.message ?? snapshot?.reason ?? null,
      license: snapshot?.model?.license ?? null,
      onRetry: () => {
        if (active() && model) void access.refetch().catch(() => {});
      },
    },
  };
}
