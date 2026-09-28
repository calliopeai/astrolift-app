"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { LIST_DEPLOYMENTS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { STATUS_BUCKET_KEYS, type StatusBucket } from "./app-deployments-format";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}
interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

/**
 * The data half of AppDeploymentsScreen: the app, its deployments (polled
 * every 30s) and environments, plus the filters and the open row, which
 * live in the URL so a refresh or a shared link keeps the operator's view.
 */
export function useAppDeployments(slug: string) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const deployments = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug: slug, limit: 100 },
    pollInterval: 30000,
  });
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });

  // Filters and the open-row id are persisted to URL search params so a
  // page refresh or shared link preserves the operator's view exactly.
  const rawBucket = searchParams.get("status") as StatusBucket | null;
  const statusBucket: StatusBucket =
    rawBucket && STATUS_BUCKET_KEYS.some((k) => k.value === rawBucket) ? rawBucket : "all";
  const envFilter = searchParams.get("env") ?? "all";
  const openId = searchParams.get("open");

  const [search, setSearch] = React.useState<string>(() => searchParams.get("q") ?? "");

  const writeParams = React.useCallback(
    (mutate: (p: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      mutate(params);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams]
  );

  function updateFilter(key: string, value: string) {
    writeParams((params) => {
      if (value && value !== "all" && value !== "") {
        params.set(key, value);
      } else {
        params.delete(key);
      }
    });
  }

  const setStatusBucket = (v: StatusBucket) => updateFilter("status", v);
  const setEnvFilter = (v: string) => updateFilter("env", v);

  const setOpenId = React.useCallback(
    (id: string | null) => {
      writeParams((params) => {
        if (id) params.set("open", id);
        else params.delete("open");
      });
    },
    [writeParams]
  );

  const toggleOpen = React.useCallback(
    (id: string) => {
      setOpenId(openId === id ? null : id);
    },
    [openId, setOpenId]
  );

  // Debounce search → URL (300 ms gives snappy typing without thrashing history).
  React.useEffect(() => {
    const id = setTimeout(() => updateFilter("q", search), 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const a = app.data?.astroliftApp ?? null;
  const allDeployments = React.useMemo(
    () => deployments.data?.astroliftDeployments ?? [],
    [deployments.data?.astroliftDeployments]
  );

  return {
    app: a,
    /** First load of the app only. */
    loading: app.loading && !a,
    deployments: allDeployments,
    deploymentsLoading: deployments.loading,
    environments: envs.data?.astroliftEnvironments ?? [],
    statusBucket,
    setStatusBucket,
    envFilter,
    setEnvFilter,
    search,
    setSearch,
    openId,
    setOpenId,
    toggleOpen,
  };
}
