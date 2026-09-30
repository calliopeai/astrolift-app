"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CLEAR_ENVIRONMENT_SETTING,
  SET_ENVIRONMENT_SETTING,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftEnvironmentSetting,
} from "@/graphql/lifecycle/lifecycle.types";

import { usePendingActions } from "@/hooks/use-pending-actions";

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface SetEnvSettingResp {
  setEnvironmentSetting: MutationResult<AstroliftEnvironmentSetting>;
}

interface ClearEnvSettingResp {
  clearEnvironmentSetting: MutationResult<AstroliftEnvironmentSetting>;
}

/**
 * Per-environment key/value overrides: the app's environments (each with
 * its settings) and the set / clear mutations, which refetch the list.
 */
export function useEnvironmentSettings(appSlug: string) {
  const { data } = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const envs = data?.astroliftEnvironments ?? [];
  const { pending: clearing, begin, finish } = usePendingActions();
  const [adding, setAdding] = React.useState(false);

  const refetch = [{ query: LIST_ENVIRONMENTS, variables: { appSlug } }];
  const [setSetting] = useMutation<SetEnvSettingResp>(SET_ENVIRONMENT_SETTING, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [clearSetting] = useMutation<ClearEnvSettingResp>(CLEAR_ENVIRONMENT_SETTING, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  /** Resolves true when the override was saved (clear the inputs). */
  async function onAdd(environmentId: string, rawKey: string, value: string): Promise<boolean> {
    const key = rawKey.trim();
    if (!key) {
      toast.error("Key is required.");
      return false;
    }
    setAdding(true);
    try {
      const { data: resp } = await setSetting({
        variables: { input: { environmentId, key, value } },
      });
      const env = resp?.setEnvironmentSetting;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Failed to save.");
        return false;
      }
      toast.success(`Set ${key}`);
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save.");
      return false;
    } finally {
      setAdding(false);
    }
  }

  async function onClear(environmentId: string, key: string) {
    const action = JSON.stringify([environmentId, key]);
    if (!begin(action)) return;
    try {
      const { data: resp } = await clearSetting({
        variables: { input: { environmentId, key } },
      });
      const env = resp?.clearEnvironmentSetting;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Failed to clear.");
        return;
      }
      toast.success(`Cleared ${key}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to clear.");
    } finally {
      finish(action);
    }
  }

  return { envs, adding, clearing, onAdd, onClear };
}
