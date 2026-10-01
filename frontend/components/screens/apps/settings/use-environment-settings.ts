"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
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

/** Current-target set/clear inputs remain unchanged; follow-up reads cannot undo acceptance. */
export function useEnvironmentSettings(appSlug: string) {
  const t = useTranslations("apps.environmentOverrides");
  const query = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const observed = Array.isArray(query.data?.astroliftEnvironments);
  const envs = query.data?.astroliftEnvironments ?? [];
  const [target, setTarget] = React.useState({ slug: appSlug, epoch: 0 });
  if (target.slug !== appSlug) setTarget({ slug: appSlug, epoch: target.epoch + 1 });
  const epoch = target.epoch;
  const [readFailure, setReadFailure] = React.useState<{ epoch: number; message: string } | null>(
    null
  );
  if (query.error && (readFailure?.epoch !== epoch || readFailure.message !== query.error.message))
    setReadFailure({ epoch, message: query.error.message });
  else if (!query.loading && !query.error && observed && readFailure?.epoch === epoch)
    setReadFailure(null);
  const currentEpoch = React.useRef<number | null>(epoch);
  React.useLayoutEffect(() => {
    currentEpoch.current = epoch;
    return () => {
      currentEpoch.current = null;
    };
  }, [epoch]);
  const { pending, begin, finish } = usePendingActions();
  const addAction = JSON.stringify([epoch, "add"]);
  const clearing = new Set<string>();
  for (const action of pending) {
    const [actionEpoch, kind, environmentId, key] = JSON.parse(action);
    if (actionEpoch === epoch && kind === "clear")
      clearing.add(JSON.stringify([environmentId, key]));
  }
  const [setSetting] = useMutation<SetEnvSettingResp>(SET_ENVIRONMENT_SETTING);
  const [clearSetting] = useMutation<ClearEnvSettingResp>(CLEAR_ENVIRONMENT_SETTING);

  function diagnostic(error: unknown, fallback: "saveFailed" | "clearFailed") {
    return error instanceof Error && error.message
      ? error.message
      : typeof error === "string" && error
        ? error
        : t(fallback);
  }
  async function refresh(approvedEpoch: number) {
    if (currentEpoch.current !== approvedEpoch) return;
    try {
      await query.refetch({ appSlug });
    } catch (error) {
      toast.warning(t("refreshWarning"), {
        description:
          error instanceof Error ? error.message : typeof error === "string" ? error : undefined,
      });
    }
  }

  /** True means the API accepted the setting, even if its subsequent read failed. */
  async function onAdd(environmentId: string, rawKey: string, value: string): Promise<boolean> {
    const key = rawKey.trim();
    if (!key) {
      toast.error(t("keyRequired"));
      return false;
    }
    if (!begin(addAction)) return false;
    try {
      let response;
      try {
        ({ data: response } = await setSetting({
          variables: { input: { environmentId, key, value } },
        }));
      } catch (error) {
        toast.error(diagnostic(error, "saveFailed"));
        return false;
      }
      const result = response?.setEnvironmentSetting;
      if (!result?.ok) {
        toast.error(result?.errors?.[0]?.message || t("saveFailed"));
        return false;
      }
      toast.success(t("saved", { key }));
      await refresh(epoch);
      return true;
    } finally {
      finish(addAction);
    }
  }

  async function onClear(environmentId: string, key: string) {
    const action = JSON.stringify([epoch, "clear", environmentId, key]);
    if (!begin(action)) return;
    try {
      let response;
      try {
        ({ data: response } = await clearSetting({ variables: { input: { environmentId, key } } }));
      } catch (error) {
        toast.error(diagnostic(error, "clearFailed"));
        return;
      }
      const result = response?.clearEnvironmentSetting;
      if (!result?.ok) {
        toast.error(result?.errors?.[0]?.message || t("clearFailed"));
        return;
      }
      toast.success(t("cleared", { key }));
      await refresh(epoch);
    } finally {
      finish(action);
    }
  }

  // Optional read-state additions keep existing pure-card callers/stories compatible.
  const reads: { target?: string; loading?: boolean; error?: string | null; onRetry?: () => void } =
    {
      target: appSlug,
      loading: query.loading && !observed,
      error:
        (query.error ? query.error.message || t("readFailed") : null) ??
        (readFailure?.epoch === epoch ? readFailure.message || t("readFailed") : null) ??
        (!query.loading && !observed ? t("readFailed") : null),
      onRetry: () => void query.refetch({ appSlug }).catch(() => {}),
    };
  return { envs, adding: pending.has(addAction), clearing, onAdd, onClear, ...reads };
}
