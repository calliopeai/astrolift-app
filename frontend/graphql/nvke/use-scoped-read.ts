"use client";

import type { DocumentNode, OperationVariables } from "@apollo/client";
import { useApolloClient } from "@apollo/client/react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

export const NVKE_REQUEST_CONTEXT = { queryDeduplication: false, redactDiagnostics: true };

/** A response belongs to one visit to one caller scope; returning to it starts a fresh read. */
export const useNvkeRead = <T, V extends OperationVariables = OperationVariables>(
  query: DocumentNode,
  variables: V,
  scopeKey: string,
  pollInterval = 0,
  expectedOrganization?: string
) => {
  const client = useApolloClient();
  const [refresh, setRefresh] = useState(0);
  const binding = JSON.stringify([scopeKey, variables, refresh, expectedOrganization]);
  const [visit, setVisit] = useState({ binding, generation: 0 });
  if (visit.binding !== binding) setVisit({ binding, generation: visit.generation + 1 });
  const key = JSON.stringify([binding, visit.generation]);
  const latest = useRef(key);
  const [snapshot, setSnapshot] = useState<{ key: string; data?: T; failed: boolean } | null>(null);
  useLayoutEffect(() => {
    latest.current = key;
    return () => {
      latest.current = "";
    };
  }, [key]);
  useEffect(() => {
    if (!scopeKey) return;
    let active = true;
    const captured = key;
    void client
      .query<T, V>({
        query,
        variables: JSON.parse(JSON.stringify(variables)) as V,
        fetchPolicy: "no-cache",
        context: { ...NVKE_REQUEST_CONTEXT, expectedNvkeOrganization: expectedOrganization },
      })
      .then((response) => {
        if (active && latest.current === captured)
          setSnapshot({
            key: captured,
            data: response.data as T | undefined,
            failed: !!response.error,
          });
      })
      .catch(() => {
        if (active && latest.current === captured) setSnapshot({ key: captured, failed: true });
      });
    return () => {
      active = false;
    };
    // The exact variables and caller are captured in key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, query, key]);
  useEffect(() => {
    if (!scopeKey || !pollInterval) return;
    const timer = setInterval(() => {
      if (!document.hidden) setRefresh((value) => value + 1);
    }, pollInterval);
    return () => clearInterval(timer);
  }, [scopeKey, pollInterval]);
  const current = scopeKey && snapshot?.key === key ? snapshot : null;
  return {
    data: current?.data,
    loading: !!scopeKey && !current,
    failed: current?.failed ?? false,
    refresh: () => setRefresh((value) => value + 1),
  };
};
