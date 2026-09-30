"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import type { CursorPage } from "@/components/data-table";
import { useCursorFeed } from "@/components/feed/use-cursor-feed";
import { useListState, useLocalListState } from "@/components/list/use-list-state";
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

import { narrows, narrowWebhooks, WEBHOOKS_LIST, webhooksVariables } from "./webhooks-list";

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
 * The subscription list (URL list state platform-wide; in memory on an
 * app's Settings tab, whose own `?section=` must survive a filter change)
 * and every row mutation behind the Webhooks screen (create, pause/resume, format, rotate, test fire, delete), plus
 * the one-time secret reveal and the inline test result those mutations
 * produce. The data half of WebhooksScreen.
 *
 * The confirm handlers (delete, rotate, test fire) throw on failure:
 * ConfirmDialog keeps itself open and toasts the message.
 */
export function useWebhooks(appSlug?: string) {
  const [reveal, setReveal] = React.useState<AstroliftWebhookSecretReveal | null>(null);
  const [testResult, setTestResult] = React.useState<AstroliftWebhookTestResult | null>(null);
  const pendingRef = React.useRef(new Set<string>());
  const [pendingRows, setPendingRows] = React.useState<ReadonlySet<string>>(new Set());

  async function runRowAction(id: string, action: () => Promise<void>) {
    if (pendingRef.current.has(id))
      throw new Error("An action is already running for this subscription");
    pendingRef.current.add(id);
    setPendingRows(new Set(pendingRef.current));
    try {
      await action();
    } finally {
      pendingRef.current.delete(id);
      setPendingRows(new Set(pendingRef.current));
    }
  }

  // `astroliftWebhookSubscriptionsPage` takes `appSlug`, `search`, `limit`
  // and `after`, no sort argument, so no column declares a `sortKey`.
  const routed = useListState(WEBHOOKS_LIST);
  const local = useLocalListState(WEBHOOKS_LIST);
  const list = appSlug ? local : routed;
  const query = useQuery<SubscriptionsPageResp>(LIST_WEBHOOKS_PAGE, {
    variables: webhooksVariables(appSlug ?? null, list.filters, list.state),
    fetchPolicy: "cache-and-network",
  });
  const data = query.data ?? query.previousData;
  const page = data?.astroliftWebhookSubscriptionsPage;
  const rows = narrowWebhooks(page?.items ?? [], list.filters);
  const refetchList = () => {
    void query.refetch();
  };

  // Refetch by operation name: the app-scoped tab and the platform-wide
  // surface are the same document at different `appSlug`s, and a name
  // covers whichever one is mounted.
  const refetchVars = ["ListWebhooksPage"];

  const [createWebhook, { loading: creating }] = useMutation<{
    createWebhookSubscription: MutationResult<AstroliftWebhookSecretReveal>;
  }>(CREATE_WEBHOOK, {
    refetchQueries: refetchVars,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });

  const [updateWebhook] = useMutation<{
    updateWebhookSubscription: MutationResult<AstroliftWebhookSubscription>;
  }>(UPDATE_WEBHOOK, {
    refetchQueries: refetchVars,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });

  const [rotateSecret, { loading: rotating }] = useMutation<{
    rotateOutboundWebhookSecret: MutationResult<AstroliftWebhookSecretReveal>;
  }>(ROTATE_OUTBOUND_WEBHOOK_SECRET, {
    refetchQueries: refetchVars,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });

  const [testFireWebhook, { loading: firing }] = useMutation<{
    testWebhookSubscription: MutationResult<AstroliftWebhookTestResult>;
  }>(TEST_FIRE_WEBHOOK, {
    refetchQueries: refetchVars,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });

  const [deleteWebhook, { loading: deleting }] = useMutation<{
    deleteWebhookSubscription: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_WEBHOOK, {
    refetchQueries: refetchVars,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });

  /** Resolves true when the subscription was created (close and reset the sheet). */
  async function onCreate({ url, eventsRaw, format }: CreateWebhookInput): Promise<boolean> {
    try {
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
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Create failed");
      return false;
    }
  }

  async function onDelete(s: AstroliftWebhookSubscription) {
    return runRowAction(s.id, async () => {
      const { data } = await deleteWebhook({ variables: { input: { id: s.id } } });
      if (data?.deleteWebhookSubscription.ok) {
        toast.success("Deleted");
      } else {
        throw new Error(data?.deleteWebhookSubscription.errors?.[0]?.message ?? "Delete failed");
      }
    });
  }

  async function onToggleActive(s: AstroliftWebhookSubscription) {
    try {
      await runRowAction(s.id, async () => {
        const next = !s.isActive;
        const { data } = await updateWebhook({
          variables: { input: { id: s.id, isActive: next, ifMatchVersion: s.version } },
        });
        if (data?.updateWebhookSubscription.ok) {
          toast.success(next ? "Resumed" : "Paused");
        } else if (
          handleVersionMismatch(data?.updateWebhookSubscription, {
            label: "webhook subscription",
            onRefresh: refetchList,
          })
        ) {
          // toast already raised by helper
        } else {
          toast.error(data?.updateWebhookSubscription.errors?.[0]?.message ?? "Toggle failed");
        }
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
    }
  }

  async function onFormatChange(s: AstroliftWebhookSubscription, next: WebhookFormat) {
    try {
      await runRowAction(s.id, async () => {
        const { data } = await updateWebhook({
          variables: { input: { id: s.id, format: next, ifMatchVersion: s.version } },
        });
        if (data?.updateWebhookSubscription.ok) {
          toast.success(`Format set to ${next}`);
        } else if (
          handleVersionMismatch(data?.updateWebhookSubscription, {
            label: "webhook subscription",
            onRefresh: refetchList,
          })
        ) {
          // toast already raised by helper
        } else {
          toast.error(data?.updateWebhookSubscription.errors?.[0]?.message ?? "Update failed");
        }
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed");
    }
  }

  async function onRotate(s: AstroliftWebhookSubscription) {
    return runRowAction(s.id, async () => {
      const { data } = await rotateSecret({ variables: { input: { id: s.id } } });
      if (data?.rotateOutboundWebhookSecret.ok && data.rotateOutboundWebhookSecret.data) {
        setReveal(data.rotateOutboundWebhookSecret.data);
        toast.success("Secret rotated — copy the new value now");
      } else {
        throw new Error(
          data?.rotateOutboundWebhookSecret.errors?.[0]?.message ?? "Rotation failed"
        );
      }
    });
  }

  async function onTestFire(s: AstroliftWebhookSubscription) {
    return runRowAction(s.id, async () => {
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
    });
  }

  function copySecret() {
    if (!reveal) return;
    navigator.clipboard.writeText(reveal.plaintextSecret);
    toast.success("Copied");
  }

  return {
    list,
    rows,
    totalCount: narrows(list.filters) ? null : (page?.totalCount ?? null),
    nextCursor: page?.nextCursor ?? null,
    loading: query.loading && !data,
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: refetchList,
    creating,
    pendingRows,
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
 * One subscription's delivery log, newest first, as a Feed that loads older
 * attempts as the reader nears the end. `subscriptionId` is required by the
 * field; `skip` holds it until the caller knows the subscription exists.
 * The data half of the deliveries feed on SubscriptionDetailView and
 * WebhookDetailScreen.
 */
export function useSubscriptionDeliveries(subscriptionId: string, { skip = false } = {}) {
  const { feed } = useCursorFeed<DeliveriesPageResp, AstroliftWebhookDelivery>(
    LIST_WEBHOOK_DELIVERIES_PAGE,
    {
      variables: { subscriptionId },
      select: (d) => d?.astroliftWebhookDeliveriesPage,
      keyOf: (d) => d.id,
      pageSize: 25,
      skip,
    }
  );
  return { deliveries: feed };
}

export type SubscriptionDeliveriesData = ReturnType<typeof useSubscriptionDeliveries>;
