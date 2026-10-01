"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { SET_RETENTION_POLICY } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import type {
  AstroliftRegisteredApp,
  AstroliftRetentionPolicy,
} from "@/graphql/registry/registry.types";

export const RETENTION_SIGNALS: { signal: string; label: string }[] = [
  { signal: "logs", label: "Logs" },
  { signal: "metrics", label: "Metrics" },
  { signal: "traces", label: "Traces" },
  { signal: "audit_events", label: "Audit Events" },
];

export const RETENTION_DAY_OPTIONS = [7, 14, 30, 60, 90, 180, 365] as const;
export const RETENTION_DEFAULT_DAYS = 30;

interface SetRetentionResp {
  setRetentionPolicy: MutationResult<AstroliftRetentionPolicy>;
}

export type RetentionSource = Pick<AstroliftRegisteredApp, "id" | "slug" | "version"> & {
  retentionPolicies: AstroliftRetentionPolicy[];
};

export function retentionSignalLabel(signal: string, translate: (key: string) => string) {
  const known = RETENTION_SIGNALS.some((item) => item.signal === signal);
  const key = signal === "audit_events" ? "auditEvents" : signal;
  return known ? translate(`signals.${key}`) : signal;
}

export function useRetentionPolicy(appSlug: string, source?: RetentionSource | null) {
  const t = useTranslations("apps.settings.retentionFlow");
  const perms = useMyPermissions();
  const allowed = (perms.loading && perms.granted.size === 0) || perms.can("app.update");
  const observed =
    source === undefined ||
    (!!source?.id && source.slug === appSlug && Array.isArray(source.retentionPolicies));
  const fingerprint = JSON.stringify([
    appSlug,
    source?.id,
    source?.version,
    source?.retentionPolicies,
    observed,
    allowed,
  ]);
  const context = React.useMemo(() => ({ fingerprint }), [fingerprint]);
  const targetFingerprint = JSON.stringify([appSlug, source?.id, observed, allowed]);
  const pendingContext = React.useMemo(() => ({ targetFingerprint }), [targetFingerprint]);
  const current = React.useRef<object | null>(context);
  React.useLayoutEffect(() => {
    current.current = context;
    return () => {
      current.current = null;
    };
  }, [context]);
  const [pending, setPending] = React.useState<{
    context: object;
    signals: Record<string, object>;
  }>({ context: pendingContext, signals: {} });
  const saving =
    pending.context === pendingContext
      ? Object.fromEntries(Object.keys(pending.signals).map((signal) => [signal, true]))
      : {};
  const [setRetention] = useMutation<SetRetentionResp>(SET_RETENTION_POLICY, {
    fetchPolicy: "no-cache",
    refetchQueries: (reply) =>
      reply.data?.setRetentionPolicy?.ok ? [{ query: GET_APP, variables: { slug: appSlug } }] : [],
    onQueryUpdated: (query) => refetchAfterMutation(query, t("refreshWarning")),
    awaitRefetchQueries: true,
  });

  async function onChange(signal: string, raw: string) {
    if (current.current !== context || !observed || !allowed) {
      toast.error(t("sourceChanged"));
      return;
    }
    const n = parseInt(raw, 10);
    if (isNaN(n) || n < 1) return;
    const operation = {};
    setPending((prior) => ({
      context: pendingContext,
      signals: { ...(prior.context === pendingContext ? prior.signals : {}), [signal]: operation },
    }));
    try {
      const { data } = await setRetention({
        variables: { input: { appSlug, signal, retentionDays: n } },
      });
      const result = data?.setRetentionPolicy;
      if (!result) {
        toast.error(t("noResponse"));
        return;
      }
      if (!result.ok) {
        toast.error(result.errors?.[0]?.message || t("saveFailed"));
        return;
      }
      toast.success(
        t("saveAccepted", { slug: appSlug, signal: retentionSignalLabel(signal, t), days: n })
      );
    } catch (err) {
      toast.error(err instanceof Error && err.message ? err.message : t("saveFailed"));
    } finally {
      setPending((prior) => {
        if (prior.context !== pendingContext || prior.signals[signal] !== operation) return prior;
        const signals = { ...prior.signals };
        delete signals[signal];
        return { context: pendingContext, signals };
      });
    }
  }
  return { saving, onChange };
}
