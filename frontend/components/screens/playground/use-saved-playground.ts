"use client";
import { useEffect, useState } from "react";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { listSavedSessions, loadSession, type SavedSession } from "./saved-sessions";

export function useSavedPlayground(starred = false) {
  const { org, loading: orgLoading } = useActiveOrg();
  const { user, loading: userLoading } = useMe();
  const scope = org?.id && user?.id ? `${org.id}:${user.id}` : "";
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    scope: string;
    rows: SavedSession[];
    error: boolean;
    ready: boolean;
  }>({ scope: "", rows: [], error: false, ready: false });
  useEffect(() => {
    if (!scope) return;
    try {
      const rows = listSavedSessions(scope)
        .map((row) => loadSession(scope, row.id))
        .filter((row): row is SavedSession => row !== null);
      setState({ scope, rows, error: false, ready: true });
    } catch {
      setState({ scope, rows: [], error: true, ready: true });
    }
  }, [scope, attempt]);
  const current = state.scope === scope ? state : null;
  return {
    sessions: current?.rows.filter((row) => !starred || row.starred) ?? [],
    loading: orgLoading || userLoading || (!!scope && !current?.ready),
    error: !orgLoading && !userLoading && (!scope || !!current?.error),
    onRetry: () => setAttempt((a) => a + 1),
  };
}
