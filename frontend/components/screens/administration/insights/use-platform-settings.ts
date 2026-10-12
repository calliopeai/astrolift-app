"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type {
  PlatformSettingFieldsFragment,
  PlatformSettingsQuery,
  ResetPlatformSettingMutation,
  ResetPlatformSettingMutationVariables,
  SetPlatformSettingMutation,
  SetPlatformSettingMutationVariables,
} from "@/graphql/__generated__/operations";
import { RESET_PLATFORM_SETTING, SET_PLATFORM_SETTING } from "@/graphql/server/server.mutations";
import { PLATFORM_SETTINGS } from "@/graphql/server/server.queries";

export type PlatformSetting = PlatformSettingFieldsFragment;

type Change = SetPlatformSettingMutation["setPlatformSetting"];

/** Install settings for Administration › Platform settings (#1861). */
export function usePlatformSettings() {
  const t = useTranslations("administration.platformSettings");
  const { data, loading, error, refetch } = useQuery<PlatformSettingsQuery>(PLATFORM_SETTINGS, {
    fetchPolicy: "network-only",
  });
  const [setSetting] = useMutation<SetPlatformSettingMutation, SetPlatformSettingMutationVariables>(
    SET_PLATFORM_SETTING
  );
  const [resetSetting] = useMutation<
    ResetPlatformSettingMutation,
    ResetPlatformSettingMutationVariables
  >(RESET_PLATFORM_SETTING);
  const [updated, setUpdated] = React.useState<Record<string, PlatformSetting>>({});
  const [pendingKey, setPendingKey] = React.useState<string | null>(null);

  const settings = (data?.astroliftPlatformSettings ?? []).map((row) => updated[row.key] ?? row);

  async function apply(key: string, run: () => Promise<Change | undefined>) {
    setPendingKey(key);
    try {
      const result = await run();
      if (!result?.ok || !result.data) {
        throw new Error(result?.errors?.[0]?.message ?? t("saveError"));
      }
      const setting = result.data.setting;
      setUpdated((prior) => ({ ...prior, [setting.key]: setting }));
      toast.success(t("saved", { key: setting.key }));
    } finally {
      setPendingKey(null);
    }
  }

  return {
    settings,
    loading,
    error: error ? new Error(error.message) : null,
    onRetry: () => {
      setUpdated({});
      void refetch();
    },
    pendingKey,
    save: (setting: PlatformSetting, value: string) =>
      apply(setting.key, async () => {
        const res = await setSetting({
          variables: { key: setting.key, value, confirmed: !!setting.warning },
        });
        return res.data?.setPlatformSetting;
      }),
    reset: (setting: PlatformSetting) =>
      apply(setting.key, async () => {
        const res = await resetSetting({
          variables: { key: setting.key, confirmed: !!setting.warning },
        });
        return res.data?.resetPlatformSetting;
      }),
  };
}
