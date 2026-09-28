"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CREATE_WEBHOOK,
  DELETE_WEBHOOK,
  ROTATE_OUTBOUND_WEBHOOK_SECRET,
  TEST_FIRE_WEBHOOK,
  UPDATE_WEBHOOK,
} from "@/graphql/operations/operations.mutations";
import {
  LIST_WEBHOOK_DELIVERIES_PAGE,
  LIST_WEBHOOKS_PAGE,
} from "@/graphql/operations/operations.queries";
import type {
  AstroliftWebhookDelivery,
  AstroliftWebhookSecretReveal,
  AstroliftWebhookSubscription,
  AstroliftWebhookTestResult,
  WebhookFormat,
} from "@/graphql/operations/operations.types";
import { handleVersionMismatch } from "@/lib/apollo/version-mismatch";

interface SubscriptionsPageResp {
  astroliftWebhookSubscriptionsPage: CursorPage<AstroliftWebhookSubscription>;
}

interface DeliveriesPageResp {
  astroliftWebhookDeliveriesPage: CursorPage<AstroliftWebhookDelivery>;
}

export interface CreateWebhookInput {
  url: string;
  /** One per line, or comma/space separated. */
  eventsRaw: string;
  format: WebhookFormat;
}

/**
 * The subscription walk and every row mutation behind the Webhooks
 * screen (create, pause/resume, format, rotate, test fire, delete), plus
 * the one-time secret reveal and the inline test result those mutations
 * produce. The data half of WebhooksScreen.
 *
 * The confirm handlers (delete, rotate, test fire) throw on failure:
 * ConfirmDialog keeps itself open and toasts the message.
 */
export function useWebhooks(appSlug?: string) {
  const [reveal, setReveal] = React.useState<AstroliftWebhookSecretReveal | null>(null);
  const [testResult, setTestResult] = React.useState<AstroliftWebhookTestResult | null>(null);

  // `astroliftWebhookSubscriptionsPage` takes `appSlug`, `search`, `limit`
  // and `after` — no sort argument, so no column declares a `sortKey` and
  // the client-side URL comparator this file used to run is gone.
  const table = useCursorTable<AstroliftWebhookSubscription>({
    query: LIST_WEBHOOKS_PAGE,
    variables: { appSlug: appSlug ?? null },
    extract: (d) => (d as SubscriptionsPageResp | undefined)?.astroliftWebhookSubscriptionsPage,
    searchVariable: "search",
    urlKey: "wh",
  });

  // Refetch by operation name: the app-scoped tab and the platform-wide
  // surface are the same document at different `appSlug`s, and a name
  // covers whichever one is mounted.
  const refetchVars = ["ListWebhooksPage"];

  const [createWebhook, { loading: creating }] = useMutation<{
    createWebhookSubscription: MutationResult<AstroliftWebhookSecretReveal>;
  }>(CREATE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [updateWebhook] = useMutation<{
    updateWebhookSubscription: MutationResult<AstroliftWebhookSubscription>;
  }>(UPDATE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [rotateSecret, { loading: rotating }] = useMutation<{
    rotateOutboundWebhookSecret: MutationResult<AstroliftWebhookSecretReveal>;
  }>(ROTATE_OUTBOUND_WEBHOOK_SECRET, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [testFireWebhook, { loading: firing }] = useMutation<{
    testWebhookSubscription: MutationResult<AstroliftWebhookTestResult>;
  }>(TEST_FIRE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [deleteWebhook, { loading: deleting }] = useMutation<{
    deleteWebhookSubscription: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  /** Resolves true when the subscription was created (close and reset the sheet). */
  async function onCreate({ url, eventsRaw, format }: CreateWebhookInput): Promise<boolean> {
    const events = eventsRaw
      .split(/\s|,/g)
      .map((e) => e.trim())
      .filter(Boolean);
    const { data } = await createWebhook({
      variables: {
        input: { url: url.trim(), events, appSlug: appSlug ?? null, format },
      },
    });
    if (data?.createWebhookSubscription.ok && data.createWebhookSubscription.data) {
      setReveal(data.createWebhookSubscription.data);
      return true;
    }
    toast.error(data?.createWebhookSubscription.errors?.[0]?.message ?? "Create failed");
    return false;
  }

  async function onDelete(s: AstroliftWebhookSubscription) {
    const { data } = await deleteWebhook({ variables: { input: { id: s.id } } });
    if (data?.deleteWebhookSubscription.ok) {
      toast.success("Deleted");
    } else {
      throw new Error(data?.deleteWebhookSubscription.errors?.[0]?.message ?? "Delete failed");
    }
  }

  async function onToggleActive(s: AstroliftWebhookSubscription) {
    const next = !s.isActive;
    const { data } = await updateWebhook({
      variables: { input: { id: s.id, isActive: next, ifMatchVersion: s.version } },
    });
    if (data?.updateWebhookSubscription.ok) {
      toast.success(next ? "Resumed" : "Paused");
    } else if (
      handleVersionMismatch(data?.updateWebhookSubscription, {
        label: "webhook subscription",
        onRefresh: () => table.refetch(),
      })
    ) {
      // toast already raised by helper
    } else {
      toast.error(data?.updateWebhookSubscription.errors?.[0]?.message ?? "Toggle failed");
    }
  }

  async function onFormatChange(s: AstroliftWebhookSubscription, next: WebhookFormat) {
    const { data } = await updateWebhook({
      variables: { input: { id: s.id, format: next, ifMatchVersion: s.version } },
    });
    if (data?.updateWebhookSubscription.ok) {
      toast.success(`Format set to ${next}`);
    } else if (
      handleVersionMismatch(data?.updateWebhookSubscription, {
        label: "webhook subscription",
        onRefresh: () => table.refetch(),
      })
    ) {
      // toast already raised by helper
    } else {
      toast.error(data?.updateWebhookSubscription.errors?.[0]?.message ?? "Update failed");
    }
  }

  async function onRotate(s: AstroliftWebhookSubscription) {
    const { data } = await rotateSecret({ variables: { input: { id: s.id } } });
    if (data?.rotateOutboundWebhookSecret.ok && data.rotateOutboundWebhookSecret.data) {
      setReveal(data.rotateOutboundWebhookSecret.data);
      toast.success("Secret rotated — copy the new value now");
    } else {
      throw new Error(data?.rotateOutboundWebhookSecret.errors?.[0]?.message ?? "Rotation failed");
    }
  }

  async function onTestFire(s: AstroliftWebhookSubscription) {
    const { data } = await testFireWebhook({ variables: { input: { id: s.id } } });
    if (data?.testWebhookSubscription.ok && data.testWebhookSubscription.data) {
      setTestResult(data.testWebhookSubscription.data);
      const code = data.testWebhookSubscription.data.statusCode;
      if (code && code >= 200 && code < 300) {
        toast.success(`Delivered: HTTP ${code}`);
      } else if (code) {
        toast.warning(`Subscriber returned HTTP ${code}`);
      } else {
        toast.error(`Transport failure: ${data.testWebhookSubscription.data.error || "unknown"}`);
      }
    } else {
      throw new Error(data?.testWebhookSubscription.errors?.[0]?.message ?? "Test fire failed");
    }
  }

  function copySecret() {
    if (!reveal) return;
    navigator.clipboard.writeText(reveal.plaintextSecret);
    toast.success("Copied");
  }

  return {
    table,
    creating,
    rotating,
    firing,
    deleting,
    reveal,
    dismissReveal: () => setReveal(null),
    copySecret,
    testResult,
    dismissTestResult: () => setTestResult(null),
    onCreate,
    onDelete,
    onToggleActive,
    onFormatChange,
    onRotate,
    onTestFire,
  };
}

export type WebhooksData = ReturnType<typeof useWebhooks>;

/**
 * The delivery walk for the subscription expanded under the table.
 * `subscriptionId` is required by the field, and the panel only mounts
 * for a selected subscription, so the walk never needs skipping. No
 * `urlKey`: the panel is transient and two subscriptions would fight over
 * the same query-string keys. The data half of SubscriptionDetailView.
 */
export function useSubscriptionDeliveries(subscriptionId: string) {
  const deliveries = useCursorTable<AstroliftWebhookDelivery>({
    query: LIST_WEBHOOK_DELIVERIES_PAGE,
    variables: { subscriptionId },
    extract: (d) => (d as DeliveriesPageResp | undefined)?.astroliftWebhookDeliveriesPage,
    searchVariable: "search",
    pageSize: 10,
  });
  return { deliveries };
}

export type SubscriptionDeliveriesData = ReturnType<typeof useSubscriptionDeliveries>;
