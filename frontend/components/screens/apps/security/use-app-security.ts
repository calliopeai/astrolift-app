"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";

import type { AstroliftRegisteredAppMutationResult } from "@/graphql/__generated__/operations";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { UPDATE_SECURITY_POLICY } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

export const DEFAULT_POLICY = {
  blockOnCriticalCves: true,
  blockOnMissingSignature: true,
  blockOnHighCveThreshold: null as number | null,
};

export type SecurityPolicyDraft = typeof DEFAULT_POLICY;

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface EventsResp {
  astroliftEvents: AstroliftEvent[];
}

export interface AstroliftEvent {
  id: string;
  eventType: string;
  payload: unknown;
  organizationId?: string | null;
  teamId?: string | null;
  projectId?: string | null;
  registeredAppId?: string | null;
  occurredAt: string;
}

export interface SigningPayload {
  image_digest?: string;
  image_tag?: string;
  signer_identity?: string;
  rekor_log_index?: number;
  rekor_entry_url?: string;
  signed_at?: string;
}

export interface SbomPayload {
  image_digest?: string;
  format?: string;
  artifact_url?: string;
  component_count?: number;
  generated_at?: string;
}

export interface ScanFinding {
  cve_id: string;
  severity: "critical" | "high" | "medium" | "low";
  package_name: string;
  package_version: string;
  fixed_in_version?: string | null;
  description?: string | null;
}

export interface ScanPayload {
  image_digest?: string;
  scanned_at?: string;
  counts?: { critical: number; high: number; medium: number; low: number };
  findings?: ScanFinding[];
}

/**
 * The app behind the security tab, its latest signing / SBOM / scan events,
 * and the supply-chain policy save. The data half of AppSecurityScreen.
 */
export function useAppSecurity(slug: string) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  // Org-wide events query — we filter client-side by registeredAppId
  // until astroliftEvents grows an appSlug arg per #313.
  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
    pollInterval: 60000,
  });

  const a = app.data?.astroliftApp ?? null;
  const allEvents = events.data?.astroliftEvents ?? [];

  const appEvents = React.useMemo(() => {
    if (!a) return [];
    return allEvents.filter((e) => e.registeredAppId === a.id);
  }, [allEvents, a]);

  const latestSigning = React.useMemo(() => {
    return appEvents.find((e) => e.eventType === "image.signed") ?? null;
  }, [appEvents]);

  const latestScan = React.useMemo(() => {
    return appEvents.find((e) => e.eventType === "image.scanned") ?? null;
  }, [appEvents]);

  const latestSbom = React.useMemo(() => {
    return appEvents.find((e) => e.eventType === "sbom.generated") ?? null;
  }, [appEvents]);

  const [updatePolicy, { loading: savingPolicy }] = useMutation<{
    updateAstroliftSecurityPolicy: AstroliftRegisteredAppMutationResult;
  }>(UPDATE_SECURITY_POLICY);

  /** Resolves true when the policy saved (the card clears its dirty flag). */
  async function onSavePolicy(policy: SecurityPolicyDraft): Promise<boolean> {
    if (!a) return false;
    const { data } = await updatePolicy({
      variables: {
        input: {
          appSlug: a.slug,
          blockOnCriticalCves: policy.blockOnCriticalCves,
          blockOnMissingSignature: policy.blockOnMissingSignature,
          blockOnHighCveThreshold: policy.blockOnHighCveThreshold,
        },
      },
    });
    return Boolean(data?.updateAstroliftSecurityPolicy?.ok);
  }

  return {
    slug,
    app: a,
    loading: app.loading,
    eventsLoading: events.loading && !events.data,
    latestSigning,
    latestSbom,
    latestScan,
    savingPolicy,
    onSavePolicy,
  };
}
