"use client";

import { toast } from "sonner";
import { useTranslations } from "next-intl";

import {
  useCreateWorkflowDeliveryChannel,
  useDeleteWorkflowDeliveryChannel,
  useUpdateWorkflowDeliveryChannel,
  useWorkflowDeliveryChannels,
} from "@/graphql/workflows/delivery.hooks";

import type { DeliveryChannelsPanelProps } from "./DeliveryChannelsPanel";

type Errors = { field: string; messages: string[] }[] | undefined;

function firstError(errors: Errors, fallback: string): string {
  const e = errors?.[0];
  return e ? e.messages.join(", ") : fallback;
}

/** Data and actions for the org admin's DeliveryChannelsPanel (#2244). */
export function useDeliveryChannels(): DeliveryChannelsPanelProps {
  const t = useTranslations("workflowDelivery");
  const { data, loading, error } = useWorkflowDeliveryChannels();
  const [create] = useCreateWorkflowDeliveryChannel();
  const [update] = useUpdateWorkflowDeliveryChannel();
  const [remove] = useDeleteWorkflowDeliveryChannel();

  return {
    channels: data?.workflowDeliveryChannels ?? [],
    loading: loading && !data,
    error: error ? t("loadFailed") : null,
    onCreate: async (input) => {
      try {
        const { data: res } = await create({ variables: input });
        const result = res?.createWorkflowDeliveryChannel;
        if (!result?.ok) return firstError(result?.errors, t("saveFailed"));
        toast.success(t("added", { name: input.name }));
        return null;
      } catch {
        return t("saveFailed");
      }
    },
    onToggle: async (guid, active) => {
      const { data: res } = await update({ variables: { channelId: guid, isActive: active } });
      if (!res?.updateWorkflowDeliveryChannel?.ok) {
        toast.error(firstError(res?.updateWorkflowDeliveryChannel?.errors, t("saveFailed")));
      }
    },
    onRemove: async (guid) => {
      const { data: res } = await remove({ variables: { channelId: guid } });
      if (!res?.deleteWorkflowDeliveryChannel?.ok) {
        throw new Error(firstError(res?.deleteWorkflowDeliveryChannel?.errors, t("saveFailed")));
      }
    },
  };
}
