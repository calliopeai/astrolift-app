"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_WEBHOOK_DELIVERIES, LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import type {
  AstroliftWebhookDelivery,
  AstroliftWebhookSubscription,
} from "@/graphql/operations/operations.types";

import { useSubscriptionDeliveries } from "./use-webhooks";

interface SubscriptionsResp {
  astroliftWebhookSubscriptions: AstroliftWebhookSubscription[];
}
interface DeliveriesResp {
  astroliftWebhookDeliveries: AstroliftWebhookDelivery[];
}
/**
 * Webhook subscription detail (#1106). Reuses the global LIST_WEBHOOKS
 * window (no singular query exists) plus the delivery log as a Feed on
 * LIST_WEBHOOK_DELIVERIES_PAGE. The data half of WebhookDetailScreen.
 */
export function useWebhookDetail(id: string) {
  const { data, loading } = useQuery<SubscriptionsResp>(LIST_WEBHOOKS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const subscription = React.useMemo(
    () => (data?.astroliftWebhookSubscriptions ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  // Skipped until the subscription resolves: `subscriptionId` is
  // non-null on the field, and this page reaches it through the global
  // LIST_WEBHOOKS window rather than a singular query.
  const { deliveries } = useSubscriptionDeliveries(id, { skip: !subscription });

  return { id, loading, subscription, deliveries };
}

export type WebhookDetailData = ReturnType<typeof useWebhookDetail>;

// Must match the subscription detail's deliveries window so the row we
// navigated from is already in cache (LIST_WEBHOOK_DELIVERIES is keyed by
// subscriptionId + limit; a singular delivery query does not exist).
const DELIVERIES_LIMIT = 50;

/**
 * Webhook delivery detail (#1106): one delivery attempt, found in its
 * subscription's deliveries window. The data half of DeliveryDetailScreen.
 */
export function useWebhookDelivery(subscriptionId: string, deliveryId: string) {
  const { data, loading } = useQuery<DeliveriesResp>(LIST_WEBHOOK_DELIVERIES, {
    variables: { subscriptionId, limit: DELIVERIES_LIMIT },
    fetchPolicy: "cache-and-network",
  });

  const delivery = React.useMemo(
    () => (data?.astroliftWebhookDeliveries ?? []).find((row) => row.id === deliveryId) ?? null,
    [data, deliveryId]
  );

  return { subscriptionId, deliveryId, loading, delivery };
}

export type WebhookDeliveryData = ReturnType<typeof useWebhookDelivery>;
