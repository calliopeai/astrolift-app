"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { usePathname, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { useListState } from "@/components/list/use-list-state";
import { CLUSTER_CERTIFICATES } from "@/graphql/clusters/clusters.queries";
import type { ClusterCertificatesQuery } from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  ADD_APP_DOMAIN,
  ADD_WILDCARD_DOMAIN,
  PAUSE_APP_INGRESS,
  RECHECK_DOMAIN_VALIDATION,
  REMOVE_APP_DOMAIN,
  RESUME_APP_INGRESS,
  SET_DOMAIN_PATH_ROUTES,
  SET_DOMAIN_REDIRECTS,
  UPLOAD_CUSTOM_DOMAIN_CERTIFICATE,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_APP_DOMAINS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";

import { APP_DOMAINS_LIST, selectDomains } from "./domains-list";

export interface RequiredDnsRecord {
  kind: string;
  name: string;
  value: string;
  ttl: number;
  propagated: boolean;
  lastCheckedAt?: string | null;
  message: string;
}

export interface DomainRedirectRule {
  id: string;
  kind: string;
  sourcePattern: string;
  destinationUrl: string;
  httpStatus: number;
  preserveQueryString: boolean;
  priority: number;
}

export interface DomainPathRoute {
  id: string;
  pathPrefix: string;
  targetWorkloadSlug: string;
  targetPort: number;
  stripPrefix: boolean;
  priority: number;
}

export interface AppDomain {
  id: string;
  hostname: string;
  certState: string;
  validationMethod: string;
  validationToken: string;
  lastCheckedAt?: string | null;
  isActive: boolean;
  registeredAppSlug: string;
  createdAt: string;
  // #397 handshake surface
  txtChallengeToken: string;
  expectedCnameTarget: string;
  isPlatformManagedZone: boolean;
  lastValidationError: string;
  requiredDnsRecords: RequiredDnsRecord[];
  // #397 cert-state surface (unhappy-path UX)
  certificateState: string;
  lastCertificateError: string;
  byoCertificateUploadedAt?: string | null;
  // #731 cert observability — populated by the cached sidecar refresh
  // worker. certExpiresAt is null when the cert hasn't been minted
  // yet or the backend can't read the upstream provider's API.
  certExpiresAt?: string | null;
  certIssuerSerial?: string;
  certObservabilityStatus?: string;
  // #685 / #686 — redirect & path-route config
  redirectRules: DomainRedirectRule[];
  pathRoutes: DomainPathRoute[];
  // #682 — wildcard / SNI surface. Optional on the type so we don't
  // crash on a stale Apollo cache entry written before these fields
  // landed in the LIST query; treat undefined as "regular domain,
  // platform-managed cert".
  isWildcard?: boolean;
  sniCertRef?: string;
  // #1621 — "no_gate" | "gated" | "ungated". Optional so a stale Apollo
  // cache entry written before the field landed doesn't crash the row;
  // undefined is treated as "nothing to say", same as no_gate.
  edgeAuthState?: string;
}

export interface WorkloadOption {
  slug: string;
  name: string;
}

export type ClusterCertificate =
  ClusterCertificatesQuery["astroliftClusterCertificates"]["certificates"][number];

interface WorkloadsResp {
  astroliftWorkloads: WorkloadOption[];
}

interface Resp {
  astroliftAppDomains: AppDomain[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

/**
 * Data half of the app domains tab: the domain + environment + workload
 * lists, every mutation the tab fires (with its toasts), and the add
 * sheet's open / wildcard state, which drive the SNI cert picker query.
 */
export function useAppDomains(slug: string) {
  const variables = { appSlug: slug };
  const domains = useQuery<Resp>(LIST_APP_DOMAINS, {
    variables,
    fetchPolicy: "cache-and-network",
    pollInterval: 30000,
  });
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables,
    fetchPolicy: "cache-and-network",
  });

  // #858 — the SNI cert picker needs the app's bound cluster (guid +
  // provider) to fetch clusterCertificates. The domains surface already
  // loads environments, each carrying its cluster binding; derive the
  // app's cluster from them. Prefer a production-named env, then any env
  // with a cluster bound. Wrapped in a memo so the CLUSTER_CERTIFICATES
  // query gets a stable clusterId.
  const sniCluster = React.useMemo(() => {
    const envList = envs.data?.astroliftEnvironments ?? [];
    const bound = envList.filter((e) => e.clusterId);
    if (bound.length === 0) return null;
    const prod = bound.find((e) => /^prod/i.test(e.name)) ?? bound[0];
    return {
      clusterId: prod.clusterId as string,
      providerSlug: prod.clusterProviderPluginSlug ?? "",
    };
  }, [envs.data]);

  const refetch = [
    { query: LIST_APP_DOMAINS, variables },
    { query: LIST_ENVIRONMENTS, variables },
  ];

  const [add, addState] = useMutation<{
    addAppDomain: MutationResult<AppDomain>;
  }>(ADD_APP_DOMAIN, { refetchQueries: refetch, awaitRefetchQueries: true });
  // #682 — wildcard variant. Same refetch pair as the regular add so
  // the new row shows up in the list immediately.
  const [addWildcard, addWildcardState] = useMutation<{
    addWildcardDomain: MutationResult<AppDomain>;
  }>(ADD_WILDCARD_DOMAIN, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [remove, removeState] = useMutation<{
    removeAppDomain: MutationResult<{ id: string; deleted: boolean }>;
  }>(REMOVE_APP_DOMAIN, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [recheck, recheckState] = useMutation<{
    recheckDomainValidation: MutationResult<AppDomain>;
  }>(RECHECK_DOMAIN_VALIDATION, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [uploadCert, uploadCertState] = useMutation<{
    uploadCustomDomainCertificate: MutationResult<AppDomain>;
  }>(UPLOAD_CUSTOM_DOMAIN_CERTIFICATE, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [pauseIngress, pauseIngressState] = useMutation<{
    pauseAppIngress: MutationResult<AstroliftAppEnvironment>;
  }>(PAUSE_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [resumeIngress, resumeIngressState] = useMutation<{
    resumeAppIngress: MutationResult<AstroliftAppEnvironment>;
  }>(RESUME_APP_INGRESS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [setRedirects, setRedirectsState] = useMutation<{
    setDomainRedirects: MutationResult<AppDomain>;
  }>(SET_DOMAIN_REDIRECTS, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [setPathRoutes, setPathRoutesState] = useMutation<{
    setDomainPathRoutes: MutationResult<AppDomain>;
  }>(SET_DOMAIN_PATH_ROUTES, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
    fetchPolicy: "cache-and-network",
  });

  // The add sheet's open state and wildcard toggle live here because they
  // are query inputs: the cert picker below only fetches while the sheet
  // is open in wildcard mode.
  // Closing the sheet resets the toggle, as the sheet resets its fields.
  const [addOpen, setAddOpenState] = React.useState(false);
  const [addIsWildcard, setAddIsWildcard] = React.useState(false);
  const setAddOpen = React.useCallback((next: boolean) => {
    setAddOpenState(next);
    if (!next) setAddIsWildcard(false);
  }, []);

  // #858 — cert picker. Cloud providers (aws/gcp/azure) can list their
  // certs; k8s_native (local issuer) and unbound apps keep the free-
  // text input. The query is skipped until the picker is actually shown
  // (wildcard sheet open, cloud provider, cluster known) so we don't
  // fire ACM list calls on every sheet open.
  const clusterId = sniCluster?.clusterId ?? null;
  const clusterProviderSlug = sniCluster?.providerSlug ?? null;
  const providerSupportsCertList =
    clusterProviderSlug === "aws" ||
    clusterProviderSlug === "gcp" ||
    clusterProviderSlug === "azure";
  const certPickerEligible = addOpen && addIsWildcard && providerSupportsCertList && !!clusterId;
  const certsQuery = useQuery<ClusterCertificatesQuery>(CLUSTER_CERTIFICATES, {
    variables: { clusterId: clusterId ?? "" },
    skip: !certPickerEligible,
    fetchPolicy: "cache-and-network",
  });
  const certsPayload = certsQuery.data?.astroliftClusterCertificates;
  // Show the combobox when the provider supports listing AND the backend
  // confirmed support; otherwise fall through to the free-text input
  // (k8s_native, unsupported clouds, or a cluster whose driver can't be
  // built). `supported=true` with an empty list still shows the
  // combobox (with its empty state) rather than reverting to free text.
  const showCertCombobox = certPickerEligible && (certsPayload?.supported ?? false);
  const certs: ClusterCertificate[] = certsPayload?.certificates ?? [];

  const busy =
    addState.loading ||
    addWildcardState.loading ||
    removeState.loading ||
    recheckState.loading ||
    uploadCertState.loading ||
    pauseIngressState.loading ||
    resumeIngressState.loading ||
    setRedirectsState.loading ||
    setPathRoutesState.loading;

  async function addDomain(
    hostname: string,
    validationMethod: string,
    isWildcard: boolean,
    sniCertRef: string
  ): Promise<boolean> {
    if (isWildcard) {
      // #682 — wildcard path uses a dedicated mutation. The
      // backend forces dns_01 (only ACME challenge that supports
      // wildcards) regardless of what we send, but we pass it
      // through anyway so the input shape is honest.
      const { data } = await addWildcard({
        variables: {
          input: {
            appSlug: slug,
            hostname,
            validationMethod: "dns_01",
            sniCertRef: sniCertRef ?? "",
          },
        },
      });
      if (data?.addWildcardDomain.ok) {
        toast.success(`Added wildcard *.${hostname}`);
        setAddOpen(false);
        return true;
      }
      toast.error(data?.addWildcardDomain.errors?.[0]?.message ?? "Add failed");
      return false;
    }
    const { data } = await add({
      variables: {
        input: {
          appSlug: slug,
          hostname,
          validationMethod: validationMethod || null,
        },
      },
    });
    if (data?.addAppDomain.ok) {
      toast.success(`Added ${hostname}`);
      setAddOpen(false);
      return true;
    }
    toast.error(data?.addAppDomain.errors?.[0]?.message ?? "Add failed");
    return false;
  }

  async function uploadCertificate(
    d: AppDomain,
    certificatePem: string,
    privateKeyPem: string
  ): Promise<boolean> {
    const { data } = await uploadCert({
      variables: {
        input: { id: d.id, certificatePem, privateKeyPem },
      },
    });
    if (data?.uploadCustomDomainCertificate.ok) {
      toast.success(`Certificate uploaded for ${d.hostname}`);
      return true;
    }
    toast.error(data?.uploadCustomDomainCertificate.errors?.[0]?.message ?? "Upload failed");
    return false;
  }

  /** Throws on failure so the confirm dialog stays open with the error. */
  async function removeDomain(d: AppDomain): Promise<void> {
    const { data } = await remove({ variables: { input: { id: d.id } } });
    if (data?.removeAppDomain.ok) {
      toast.success(`Removed ${d.hostname}`);
    } else {
      throw new Error(data?.removeAppDomain.errors?.[0]?.message ?? "Remove failed");
    }
  }

  async function recheckDomain(d: AppDomain): Promise<void> {
    const { data } = await recheck({ variables: { input: { id: d.id } } });
    if (data?.recheckDomainValidation.ok) {
      toast.success(`Recheck queued for ${d.hostname}`);
    } else {
      toast.error(data?.recheckDomainValidation.errors?.[0]?.message ?? "Recheck failed");
    }
  }

  async function saveRedirects(d: AppDomain, rules: DomainRedirectRule[]): Promise<boolean> {
    const { data } = await setRedirects({
      variables: {
        input: {
          domainId: d.id,
          rules: rules.map((r) => ({
            kind: r.kind,
            sourcePattern: r.sourcePattern,
            destinationUrl: r.destinationUrl,
            httpStatus: r.httpStatus,
            preserveQueryString: r.preserveQueryString,
            priority: r.priority,
          })),
        },
      },
    });
    if (data?.setDomainRedirects.ok) {
      toast.success(`Redirects updated for ${d.hostname}`);
      return true;
    }
    toast.error(data?.setDomainRedirects.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  async function savePathRoutes(d: AppDomain, routes: DomainPathRoute[]): Promise<boolean> {
    const { data } = await setPathRoutes({
      variables: {
        input: {
          domainId: d.id,
          routes: routes.map((r) => ({
            pathPrefix: r.pathPrefix,
            targetWorkloadSlug: r.targetWorkloadSlug,
            targetPort: r.targetPort,
            stripPrefix: r.stripPrefix,
            priority: r.priority,
          })),
        },
      },
    });
    if (data?.setDomainPathRoutes.ok) {
      toast.success(`Path routes updated for ${d.hostname}`);
      return true;
    }
    toast.error(data?.setDomainPathRoutes.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  async function toggleIngress(env: AstroliftAppEnvironment): Promise<void> {
    if (env.ingressPaused) {
      const { data } = await resumeIngress({
        variables: { input: { id: env.id } },
      });
      if (data?.resumeAppIngress.ok) {
        toast.success(`Ingress resumed for ${env.name}.`);
      } else {
        toast.error(data?.resumeAppIngress.errors?.[0]?.message ?? "Resume failed.");
      }
    } else {
      const { data } = await pauseIngress({
        variables: { input: { id: env.id } },
      });
      if (data?.pauseAppIngress.ok) {
        toast.success(`Ingress paused for ${env.name}.`);
      } else {
        toast.error(data?.pauseAppIngress.errors?.[0]?.message ?? "Pause failed.");
      }
    }
  }

  // The tab's list: URL state, answered in the browser (domains-list.ts).
  const list = useListState(APP_DOMAINS_LIST);
  const all = domains.data?.astroliftAppDomains ?? [];
  const { rows, totalCount } = selectDomains(all, list.filters, list.state);
  // The picked domain is `?domain=<id>`; a row links there, keeping the
  // list's own params, so the handshake panel under the list follows it.
  const params = useSearchParams();
  const pathname = usePathname() ?? "";
  const qs = params?.toString() ?? "";
  const domainHref = (d: AppDomain) => {
    const p = new URLSearchParams(qs);
    p.set("domain", d.id);
    return `${pathname}?${p.toString()}`;
  };

  return {
    loading: domains.loading,
    /** The domains query failed with nothing cached. */
    error: domains.data ? null : (domains.error ?? null),
    refetch: (): void => {
      void domains.refetch();
    },
    domains: all,
    list,
    rows,
    totalCount,
    pickedId: params?.get("domain") ?? null,
    domainHref,
    environments: envs.data?.astroliftEnvironments ?? [],
    workloadOptions: workloads.data?.astroliftWorkloads ?? [],
    busy,
    addOpen,
    setAddOpen,
    addIsWildcard,
    setAddIsWildcard,
    clusterProviderSlug,
    showCertCombobox,
    certs,
    certsLoading: certsQuery.loading,
    addDomain,
    uploadCertificate,
    removeDomain,
    recheckDomain,
    saveRedirects,
    savePathRoutes,
    toggleIngress,
  };
}

export type AppDomainsState = ReturnType<typeof useAppDomains>;
