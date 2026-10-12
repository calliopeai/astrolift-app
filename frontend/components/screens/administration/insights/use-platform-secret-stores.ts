"use client";

import { useLazyQuery, useQuery } from "@apollo/client/react";

import type {
  PlatformSecretInventoryQuery,
  PlatformSecretInventoryQueryVariables,
  PlatformSecretStoresQuery,
} from "@/graphql/__generated__/operations";
import { PLATFORM_SECRET_INVENTORY, PLATFORM_SECRET_STORES } from "@/graphql/server/server.queries";

export type PlatformSecretStore =
  PlatformSecretStoresQuery["astroliftPlatformSecretStores"][number];
export type PlatformSecretInventory = NonNullable<
  PlatformSecretInventoryQuery["astroliftPlatformSecretInventory"]
>;

/** Agent secret stores and one organization's refs, read-only (#1861). */
export function usePlatformSecretStores() {
  const stores = useQuery<PlatformSecretStoresQuery>(PLATFORM_SECRET_STORES, {
    fetchPolicy: "network-only",
  });
  const [inspect, inventory] = useLazyQuery<
    PlatformSecretInventoryQuery,
    PlatformSecretInventoryQueryVariables
  >(PLATFORM_SECRET_INVENTORY, { fetchPolicy: "network-only" });
  return {
    stores: stores.data?.astroliftPlatformSecretStores ?? [],
    storesLoading: stores.loading,
    storesError: stores.error ? new Error(stores.error.message) : null,
    inventory: inventory.data?.astroliftPlatformSecretInventory ?? null,
    inventoryLoading: inventory.loading,
    inventoryError: inventory.error ? new Error(inventory.error.message) : null,
    inspect: (organizationSlug: string) => void inspect({ variables: { organizationSlug } }),
  };
}

export type PlatformSecretStoresProps = ReturnType<typeof usePlatformSecretStores>;
