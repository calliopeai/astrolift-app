"use client";

import * as React from "react";
import { useLazyQuery } from "@apollo/client/react";
import { useMe } from "@/graphql/user/user.hooks";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { GET_MANAGED_RESOURCE } from "@/graphql/services/resource-reads.queries";
import type {
  GetManagedResourceQuery,
  ManagedResourceContextFragment,
} from "@/graphql/__generated__/operations";

/** The selected GUID needs a fresh current-identity read before downstream runtime reads. */
export function useManagedResourceContext(id: string | null, appSlug: string) {
  const { user } = useMe();
  const { org } = useActiveOrg();
  const orgId = org?.id;
  const binding = user && orgId && id ? `${user.id}:${orgId}:${appSlug}:${id}` : "";
  const [scope, setScope] = React.useState({ binding, epoch: 0 });
  const [result, setResult] = React.useState<{
    key: string;
    row: ManagedResourceContextFragment | null;
  } | null>(null);
  const [refresh, setRefresh] = React.useState(0);
  if (scope.binding !== binding) {
    setScope({ binding, epoch: scope.epoch + 1 });
    setResult(null);
  }
  const key = scope.binding === binding && binding ? `${binding}:${scope.epoch}:${refresh}` : "";
  const [execute] = useLazyQuery<GetManagedResourceQuery>(GET_MANAGED_RESOURCE, {
    fetchPolicy: "no-cache",
  });
  const generation = React.useRef(0);
  React.useLayoutEffect(() => {
    generation.current += 1;
  }, [key]);
  React.useEffect(() => {
    if (!key || !id || !orgId) return;
    const request = ++generation.current;
    let disposed = false;
    const finish = (row: ManagedResourceContextFragment | null) => {
      if (!disposed && request === generation.current) setResult({ key, row });
    };
    void execute({ variables: { id }, context: { queryDeduplication: false } }).then(
      ({ data, error }) => {
        const row = data?.astroliftManagedService;
        finish(
          !error &&
            row?.id === id &&
            row.organizationId === orgId &&
            row.ownerScope === "app" &&
            row.registeredAppSlug === appSlug &&
            row.registeredAppId
            ? row
            : null
        );
      },
      () => finish(null)
    );
    return () => {
      disposed = true;
    };
  }, [key, id, orgId, appSlug, execute]);
  const observed = result?.key === key && Boolean(key);
  return {
    current: observed ? result.row : null,
    loading: Boolean(key) && !observed,
    refused: Boolean(id) && (!key || (observed && !result.row)),
    scopeKey: key,
    retry: () => setRefresh((value) => value + 1),
  };
}
