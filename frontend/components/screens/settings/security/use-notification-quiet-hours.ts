"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import type {
  MyNotificationQuietHoursQuery,
  SetMyNotificationQuietHoursMutation,
  SetMyNotificationQuietHoursMutationVariables,
} from "@/graphql/__generated__/operations";
import { MY_NOTIFICATION_QUIET_HOURS } from "@/graphql/operations/notification-quiet-hours.queries";
import { SET_MY_NOTIFICATION_QUIET_HOURS } from "@/graphql/operations/notification-quiet-hours.mutations";
import type { NotificationQuietHoursPanelProps } from "./NotificationQuietHoursPanel";

export const useNotificationQuietHours = (): NotificationQuietHoursPanelProps => {
  const query = useQuery<MyNotificationQuietHoursQuery>(MY_NOTIFICATION_QUIET_HOURS, {
    fetchPolicy: "network-only",
    pollInterval: 60000,
  });
  const [mutate, mutation] = useMutation<
    SetMyNotificationQuietHoursMutation,
    SetMyNotificationQuietHoursMutationVariables
  >(SET_MY_NOTIFICATION_QUIET_HOURS, { fetchPolicy: "no-cache" });
  const [saveError, setSaveError] = useState(false);
  const [saved, setSaved] = useState(false);
  return {
    preferences: query.data?.astroliftMyNotificationQuietHours ?? null,
    loading: query.loading && !query.data,
    error: Boolean(query.error),
    saving: mutation.loading,
    saveError,
    saved,
    onRetry: () => {
      void query.refetch().catch(() => {});
    },
    onSave: async (input) => {
      setSaveError(false);
      setSaved(false);
      try {
        const response = await mutate({ variables: { input } });
        if (!response.data?.setNotificationQuietHours.ok) {
          setSaveError(true);
          return;
        }
        await query.refetch();
        setSaved(true);
      } catch {
        setSaveError(true);
      }
    },
  };
};
