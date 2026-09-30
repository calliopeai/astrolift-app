"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import type { CursorPage } from "@/components/data-table";
import { useLocalListState } from "@/components/list/use-list-state";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import {
  DEPROVISION_MANAGED_SERVICE,
  PROVISION_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import {
  LIST_MANAGED_SERVICES,
  LIST_MANAGED_SERVICES_PAGE,
} from "@/graphql/services/services.queries";

import { useCursorList } from "../use-cursor-list";
import { APP_MANAGED_SERVICES_LIST } from "./managed-services-list";

export interface ManagedService {
  id: string;
  name: string;
  kind: string;
  variant: string;
  status: string;
  statusError: string;
  config: Record<string, unknown>;
  environmentName: string;
  registeredAppSlug: string;
  createdAt: string;
  updatedAt: string;
  lastActionAt: string | null;
  lastActionKind: string;
}

export interface ProvisionInput {
  environmentName: string;
  kind: string;
  name: string | null;
  variant: string | null;
}

export interface DeprovisionOptions {
  deleteData: boolean;
  forceDestroy: boolean;
}

interface PageResp {
  astroliftManagedServicesPage: CursorPage<ManagedService>;
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

/**
 * The app's managed-service walk, its environments, and the provision /
 * deprovision mutations. The data half of ManagedServicesScreen.
 */
export function useManagedServices(slug: string) {
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });
  // `astroliftManagedServicesPage` searches the service name, kind,
  // variant and environment, and takes no sort argument, so no column
  // declares a `sortKey`. The flat field it replaces is deprecated for
  // applying no ordering at all — row order was whatever Postgres
  // returned — so paging also makes the list stable between polls. The
  // 15s poll stays: a provisioning service settles while an operator
  // watches this page.
  // The list state is in memory: the section lives under
  // `?section=managed-services`, which a URL list state would drop.
  const services = useCursorList<ManagedService>({
    query: LIST_MANAGED_SERVICES_PAGE,
    variables: { appSlug: slug, environmentName: null },
    extract: (d) => (d as PageResp | undefined)?.astroliftManagedServicesPage,
    list: useLocalListState(APP_MANAGED_SERVICES_LIST),
    pollInterval: 15000,
  });

  const refetch = [
    "ListManagedServicesPage",
    {
      query: LIST_MANAGED_SERVICES,
      variables: { appSlug: slug, environmentName: null },
    },
  ];

  const [provision, provisionState] = useMutation<{
    provisionManagedService: MutationResult<ManagedService>;
  }>(PROVISION_MANAGED_SERVICE, {
    refetchQueries: refetch,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });
  const [deprovision, deprovisionState] = useMutation<{
    deprovisionManagedService: MutationResult<{ id: string; deleted: boolean }>;
  }>(DEPROVISION_MANAGED_SERVICE, {
    refetchQueries: refetch,
    onQueryUpdated: refetchAfterMutation,
    awaitRefetchQueries: true,
  });

  /** Resolves true when the provision was accepted (close the sheet). */
  async function onProvision(input: ProvisionInput): Promise<boolean> {
    try {
      const { data } = await provision({
        variables: { input: { appSlug: slug, ...input } },
      });
      if (data?.provisionManagedService.ok) {
        toast.success(`Provisioning ${input.kind}`);
        return true;
      }
      toast.error(data?.provisionManagedService.errors?.[0]?.message ?? "Provision failed");
      return false;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Provision failed");
      return false;
    }
  }

  /** Resolves true when the deprovision was accepted (close the dialog). */
  async function onDeprovision(s: ManagedService, opts: DeprovisionOptions): Promise<boolean> {
    const { deleteData, forceDestroy } = opts;
    try {
      const { data } = await deprovision({
        variables: {
          input: {
            id: s.id,
            deleteData,
            forceDestroy,
          },
        },
      });
      if (data?.deprovisionManagedService.ok) {
        const verb =
          deleteData && forceDestroy
            ? "Force-deprovisioning + deleting data for"
            : deleteData
              ? "Deprovisioning + deleting data for"
              : forceDestroy
                ? "Force-deprovisioning"
                : "Deprovisioning";
        toast.success(`${verb} ${s.name}`);
        return true;
      }
      throw new Error(data?.deprovisionManagedService.errors?.[0]?.message ?? "Deprovision failed");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Deprovision failed");
      return false;
    }
  }

  return {
    ...services,
    envs: envs.data?.astroliftEnvironments ?? [],
    busy: provisionState.loading || deprovisionState.loading,
    deprovisioning: deprovisionState.loading,
    onProvision,
    onDeprovision,
  };
}
