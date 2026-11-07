"use client";

import { useLazyQuery } from "@apollo/client/react";
import type { ErrorLike, OperationVariables } from "@apollo/client";
import * as React from "react";
import { useCursorTable, type CursorTableOptions, type ScopedCursorRead } from "./use-cursor-table";

/** Observe each actor-bound server page separately, including identical queries after a scope switch. */
export function useScopedCursorTable<T, V extends OperationVariables = OperationVariables>(
  options: CursorTableOptions<T, V>,
  scopeKey: string,
  requestContext?: Record<string, unknown>,
  validateResponse?: (data: unknown, variables: Record<string, unknown>) => boolean
) {
  const [load] = useLazyQuery(options.query, { fetchPolicy: "no-cache" });
  const [snapshot, setSnapshot] = React.useState<Omit<ScopedCursorRead, "refresh"> | null>(null);
  const [refreshEpoch, setRefreshEpoch] = React.useState(0);
  const refresh = () => setRefreshEpoch((epoch) => epoch + 1);
  const controller = useCursorTable<T, V>({
    ...options,
    requestScopeKey: scopeKey,
    externalRead: snapshot ? { ...snapshot, refresh } : undefined,
  });
  const variables = controller.requestVariables ?? {};
  const binding = JSON.stringify([scopeKey, variables, refreshEpoch, options.skip, requestContext]);
  const [visit, setVisit] = React.useState({ binding, generation: 0 });
  if (visit.binding !== binding) setVisit({ binding, generation: visit.generation + 1 });
  const key = JSON.stringify([binding, visit.generation]);
  const latest = React.useRef(key);
  React.useLayoutEffect(() => {
    latest.current = key;
  }, [key]);
  React.useEffect(() => {
    if (!scopeKey || options.skip) return;
    let active = true;
    const captured = key;
    const capturedVariables = JSON.parse(JSON.stringify(variables)) as Record<string, unknown>;
    Promise.resolve().then(async () => {
      if (!active || latest.current !== captured) return;
      setSnapshot((current) => ({
        scopeKey,
        variables: capturedVariables,
        data: current?.scopeKey === scopeKey ? current.data : undefined,
        loading: true,
      }));
      try {
        const response = await load({
          variables: capturedVariables,
          context: { ...requestContext, queryDeduplication: false },
        });
        if (!active || latest.current !== captured) return;
        if (validateResponse && !validateResponse(response.data, capturedVariables)) {
          setSnapshot({
            scopeKey,
            variables: capturedVariables,
            loading: false,
            error: new Error("Response does not match the requested scope"),
          });
          return;
        }
        setSnapshot({
          scopeKey,
          variables: capturedVariables,
          data: response.data,
          error: response.error,
          loading: false,
        });
      } catch (error) {
        if (!active || latest.current !== captured) return;
        setSnapshot({
          scopeKey,
          variables: capturedVariables,
          error: error as ErrorLike,
          loading: false,
        });
      }
    });
    return () => {
      active = false;
      if (latest.current === captured) latest.current = "";
    };
    // Variables are represented by their exact stable JSON in key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, load, validateResponse]);
  return { ...controller, retry: refresh, refetch: refresh };
}
