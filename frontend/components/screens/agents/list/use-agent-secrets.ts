"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import {
  DELETE_AGENT_SECRET_VALUE,
  REMOVE_AGENT_SECRET_REF,
  REVEAL_AGENT_SECRET_VALUE,
  SET_AGENT_SECRET_VALUE,
  UPSERT_AGENT_SECRET_REF,
} from "@/graphql/agents/agents.mutations";
import {
  AGENT_ENV_SPEC_SECRET_STATUS,
  AGENT_SECRET_STATUS_PAGE,
} from "@/graphql/agents/agents.queries";
import type { AstroliftAgentSecretStatus } from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { usePendingActions } from "@/hooks/use-pending-actions";

interface SecretStatusResp {
  agentEnvironmentSpecSecretStatus: AstroliftAgentSecretStatus[];
}
interface SecretMutationResp {
  [key: string]: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { envVar: string; uri: string; exists: boolean } | null;
  };
}
interface SecretRevealResp {
  revealAgentSecretValue: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { envVar: string; uri: string; value: string; provider: string } | null;
  };
}

function firstError(errs: { message: string }[]): string {
  return errs[0]?.message ?? "unknown error";
}

/**
 * The secret refs of an env spec with their Set / Missing / Error status, and
 * the value/ref mutations behind them (#1173): set/rotate, delete, bind,
 * unbind, and the audited Reveal (auto-hides after 30 seconds). The whole
 * status read runs only while `open`; the agent's Secrets tab passes false
 * and reads its own page. A mutation re-reads whichever of the two is on
 * screen. The data half of AgentSecretsView. Handlers the view reacts to
 * resolve true on success.
 */
export function useAgentSecrets(envSpecSlug: string, open: boolean | undefined) {
  const client = useApolloClient();
  const { data, loading } = useQuery<SecretStatusResp>(AGENT_ENV_SPEC_SECRET_STATUS, {
    variables: { slug: envSpecSlug },
    skip: !open || !envSpecSlug,
    fetchPolicy: "cache-and-network",
  });
  const refetch = () =>
    client.refetchQueries({ include: [AGENT_ENV_SPEC_SECRET_STATUS, AGENT_SECRET_STATUS_PAGE] });
  const rows = data?.agentEnvironmentSpecSecretStatus ?? [];
  // The backend refuses a ref outside agents/<org guid>/ (#1921).
  const { org } = useActiveOrg();
  const refNamespace = `agents/${org?.id ?? "<organization id>"}`;

  const [reveals, setReveals] = React.useState<Record<string, string>>({});
  const [revealScope, setRevealScope] = React.useState({ envSpecSlug, open });
  if (revealScope.envSpecSlug !== envSpecSlug || revealScope.open !== open) {
    setRevealScope({ envSpecSlug, open });
    setReveals({});
  }
  const revealTimers = React.useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const { pending, begin, finish } = usePendingActions();
  const busyVar = pending.values().next().value ?? "";
  const revealGeneration = React.useRef(0);

  const [setSecret] = useMutation<SecretMutationResp>(SET_AGENT_SECRET_VALUE);
  const [deleteSecret] = useMutation<SecretMutationResp>(DELETE_AGENT_SECRET_VALUE);
  const [upsertRef] = useMutation<SecretMutationResp>(UPSERT_AGENT_SECRET_REF);
  const [removeRef] = useMutation<SecretMutationResp>(REMOVE_AGENT_SECRET_REF);
  const [revealSecret] = useMutation<SecretRevealResp>(REVEAL_AGENT_SECRET_VALUE);

  /** Drop values, cancel timers and invalidate reveals that are still in flight. */
  const clearReveals = React.useCallback(() => {
    revealGeneration.current += 1;
    Object.values(revealTimers.current).forEach(clearTimeout);
    revealTimers.current = {};
    setReveals({});
  }, []);

  React.useEffect(() => {
    return () => {
      revealGeneration.current += 1;
      Object.values(revealTimers.current).forEach(clearTimeout);
      revealTimers.current = {};
    };
  }, [envSpecSlug, open]);

  async function onSave(envVar: string, value: string): Promise<boolean> {
    if (!value) {
      toast.error("Enter a value first");
      return false;
    }
    const action = envVar;
    if (!begin(action)) return false;
    try {
      const res = await setSecret({
        variables: { slug: envSpecSlug, envVar, value },
      });
      const payload = res.data?.setAgentSecretValue;
      if (!payload?.ok) {
        toast.error(`Set ${envVar} failed: ${firstError(payload?.errors ?? [])}`);
        return false;
      }
      toast.success(`Saved ${envVar}`);
      await refetch();
      return true;
    } catch (err) {
      toast.error(`Set ${envVar} failed: ${err instanceof Error ? err.message : String(err)}`);
      return false;
    } finally {
      finish(action);
    }
  }

  async function onDeleteValue(target: AstroliftAgentSecretStatus) {
    const action = target.envVar;
    if (!begin(action)) return;
    try {
      const res = await deleteSecret({
        variables: { slug: envSpecSlug, envVar: target.envVar },
      });
      const payload = res.data?.deleteAgentSecretValue;
      if (!payload?.ok) {
        toast.error(`Delete ${target.envVar} failed: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      toast.success(`Deleted ${target.envVar}`);
      await refetch();
    } catch (err) {
      toast.error(
        `Delete ${target.envVar} failed: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      finish(action);
    }
  }

  async function onUpsertRef(rawEnvVar: string, uri: string): Promise<boolean> {
    const envVar = rawEnvVar.trim();
    if (!envVar || !uri.trim()) {
      toast.error("Environment variable and provider URI are required");
      return false;
    }
    const action = `ref:${envVar}`;
    if (!begin(action)) return false;
    try {
      const res = await upsertRef({
        variables: { slug: envSpecSlug, envVar, uri: uri.trim() },
      });
      const payload = res.data?.upsertAgentSecretRef;
      if (!payload?.ok) {
        toast.error(`Save ref failed: ${firstError(payload?.errors ?? [])}`);
        return false;
      }
      toast.success(`Saved binding ${envVar}`);
      await refetch();
      return true;
    } catch (err) {
      toast.error(`Save ref failed: ${err instanceof Error ? err.message : String(err)}`);
      return false;
    } finally {
      finish(action);
    }
  }

  async function onRemoveRef(row: AstroliftAgentSecretStatus) {
    const action = `remove:${row.envVar}`;
    if (!begin(action)) return;
    try {
      const res = await removeRef({ variables: { slug: envSpecSlug, envVar: row.envVar } });
      const payload = res.data?.removeAgentSecretRef;
      if (!payload?.ok) {
        throw new Error(`Remove ref failed: ${firstError(payload?.errors ?? [])}`);
      }
      toast.success(`Removed binding ${row.envVar}`);
      await refetch();
    } catch (err) {
      toast.error(`Remove ref failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      finish(action);
    }
  }

  async function onReveal(row: AstroliftAgentSecretStatus) {
    if (reveals[row.envVar]) {
      clearTimeout(revealTimers.current[row.envVar]);
      delete revealTimers.current[row.envVar];
      setReveals((current) => {
        const next = { ...current };
        delete next[row.envVar];
        return next;
      });
      return;
    }
    const action = `reveal:${row.envVar}`;
    if (!begin(action)) return;
    const generation = revealGeneration.current;
    try {
      const res = await revealSecret({ variables: { slug: envSpecSlug, envVar: row.envVar } });
      if (generation !== revealGeneration.current) return;
      const payload = res.data?.revealAgentSecretValue;
      if (!payload?.ok || !payload.data) {
        toast.error(`Reveal failed: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      setReveals((current) => ({ ...current, [row.envVar]: payload.data!.value }));
      clearTimeout(revealTimers.current[row.envVar]);
      revealTimers.current[row.envVar] = setTimeout(() => {
        delete revealTimers.current[row.envVar];
        setReveals((current) => {
          const next = { ...current };
          delete next[row.envVar];
          return next;
        });
      }, 30_000);
    } catch (err) {
      if (generation !== revealGeneration.current) return;
      toast.error(`Reveal failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      finish(action);
    }
  }

  return {
    rows,
    loading,
    refNamespace,
    reveals,
    busyVar,
    pending,
    clearReveals,
    onSave,
    onDeleteValue,
    onUpsertRef,
    onRemoveRef,
    onReveal,
  };
}
