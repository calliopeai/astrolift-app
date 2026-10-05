"use client";

import * as React from "react";
import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import type {
  UpdateManagedDomainDefaultsMutation,
  UpdateManagedDomainDefaultsMutationVariables,
} from "@/graphql/__generated__/operations";
import { UPDATE_MANAGED_DOMAIN_DEFAULTS } from "@/graphql/domains/domains.queries";
import type { DomainDefaultChoice, DomainDefaultSettingsProps } from "./DomainDefaultSettings";
import { domainDefaultReasonKey } from "./domain-default-reasons";
import type { ManagedDomainState } from "./use-managed-domain";

const choices = new Set<DomainDefaultChoice>(["none", "tenant_apps", "preview_envs", "both"]);
export function useDomainDefaultSettings(
  state: ManagedDomainState
): DomainDefaultSettingsProps | null {
  const t = useTranslations("domainDefaults"),
    d = useTranslations("managedDomains");
  const [write] = useMutation<
    UpdateManagedDomainDefaultsMutation,
    UpdateManagedDomainDefaultsMutationVariables
  >(UPDATE_MANAGED_DOMAIN_DEFAULTS);
  const [busy, setBusy] = React.useState(false),
    [uncertain, setUncertain] = React.useState(false);
  const [actionMessage, setActionMessage] = React.useState<string | null>(null),
    [actionError, setActionError] = React.useState<string | null>(null);
  const mounted = React.useRef(true),
    sending = React.useRef(false),
    generation = React.useRef(0);
  React.useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const { domain, diagnostics } = state;
  const factsCurrent = Boolean(
    domain &&
    diagnostics &&
    diagnostics.id === domain.id &&
    diagnostics.version === domain.version &&
    diagnostics.zone === domain.zone &&
    !state.loading &&
    !state.error &&
    !state.diagnosticsLoading &&
    !state.diagnosticsError &&
    !state.removed
  );
  const actions = factsCurrent ? diagnostics!.actions : null;
  const fingerprint = actions?.defaultsFingerprint ?? null;
  const fingerprintValid = /^[a-f0-9]{64}$/.test(fingerprint ?? "");
  const useDeclared =
    domain && choices.has(domain.defaultFor as DomainDefaultChoice)
      ? (domain.defaultFor as DomainDefaultChoice)
      : null;
  const current = factsCurrent && fingerprintValid && useDeclared !== null;
  async function onSave(
    choice: DomainDefaultChoice,
    expectedVersion: number,
    expectedFingerprint: string
  ) {
    if (
      sending.current ||
      uncertain ||
      !current ||
      !domain ||
      !choices.has(choice) ||
      expectedVersion !== domain.version ||
      expectedFingerprint !== fingerprint ||
      !actions?.canUpdate ||
      (choice !== "none" && !actions.canSetDefaults)
    )
      throw new Error(t("reasonChanged"));
    sending.current = true;
    setBusy(true);
    setActionMessage(null);
    setActionError(null);
    const epoch = ++generation.current;
    try {
      const result = await write({
        variables: {
          input: {
            id: domain.id,
            defaultFor: choice,
            expectedVersion,
            expectedDefaultsFingerprint: expectedFingerprint,
            dnsConfig: null,
            isWildcardManaged: null,
          },
        },
      });
      if (!mounted.current || epoch !== generation.current) return;
      const payload = result.data?.updateManagedDomain;
      if (payload?.ok === false && payload.errors.length && !payload.data) {
        setActionError(t(domainDefaultReasonKey(payload.errors[0].message)));
        setUncertain(true);
      } else if (payload?.ok === true) {
        const value = payload.data;
        const correlated =
          Array.isArray(payload.errors) &&
          payload.errors.length === 0 &&
          value &&
          value.id === domain.id &&
          value.zone === domain.zone &&
          value.organizationSlug === domain.organizationSlug &&
          Number.isSafeInteger(value.version) &&
          value.version >= expectedVersion &&
          value.defaultFor === choice;
        setActionMessage(correlated ? t("applied") : d("acceptedUnverified"));
        if (!correlated) setUncertain(true);
      } else {
        setUncertain(true);
        setActionError(t("uncertain"));
      }
      const refreshed = await state.onReloadCurrentDomain();
      if (mounted.current && epoch === generation.current && !refreshed) {
        setUncertain(true);
        setActionError(d("refreshFailed"));
      }
    } catch {
      if (mounted.current && epoch === generation.current) {
        setUncertain(true);
        setActionError(t("uncertain"));
        await state.onReloadCurrentDomain();
      }
    } finally {
      if (mounted.current && epoch === generation.current) {
        sending.current = false;
        setBusy(false);
      }
    }
  }
  async function onRefresh() {
    if (sending.current || !mounted.current) return;
    sending.current = true;
    setBusy(true);
    const epoch = ++generation.current;
    const refreshed = await state.onReloadCurrentDomain();
    if (mounted.current && epoch === generation.current) {
      sending.current = false;
      setBusy(false);
      if (refreshed) {
        setUncertain(false);
        setActionError(null);
      } else {
        setUncertain(true);
        setActionError(d("refreshFailed"));
      }
    }
  }
  if (!domain || !useDeclared || state.removed) return null;
  const effective = (key: string) => {
    const check = diagnostics?.checks.find((row) => row.key === key);
    return factsCurrent && check?.observed.length === 1 ? check.observed[0] : null;
  };
  return {
    zone: domain.zone,
    version: domain.version,
    defaultsFingerprint: current ? fingerprint : null,
    declared: useDeclared,
    effectiveTenant: effective("effective_tenant_apps_domain"),
    effectivePreview: effective("effective_preview_domain"),
    canUpdate: current && actions?.canUpdate === true,
    canSetDefaults: current && actions?.canSetDefaults === true,
    loading: state.loading || state.diagnosticsLoading,
    busy: busy || state.deleting || state.verifying || state.revalidating,
    reason: actions?.defaultUpdateReason || (!factsCurrent ? "CURRENT_DEFAULTS_UNAVAILABLE" : null),
    uncertain,
    actionMessage,
    actionError,
    onSave,
    onRefresh,
  };
}
