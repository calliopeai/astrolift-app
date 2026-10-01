"use client";

import {
  AlertTriangleIcon,
  KeyRoundIcon,
  Loader2Icon,
  PencilIcon,
  RocketIcon,
  ShieldIcon,
} from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Section } from "@/components/ui/section";
import { cn } from "@/lib/utils";

import { Field } from "./Field";
import {
  type CognitoUserPool,
  type CognitoUserPoolClient,
  type IngressAuthConfig,
  poolIdFromArn,
} from "./types";
import type { useIngressAuth } from "./use-ingress-auth";

export type IngressAuthViewProps = ReturnType<typeof useIngressAuth>;

/**
 * Accessible on/off switch. The repo has no shadcn/radix Switch
 * primitive, so this is a small local toggle styled with the same
 * Tailwind tokens the rest of the settings surface uses — not a new
 * shared component. The "on" track is emerald so the enabled state
 * reads as a security control rather than a neutral preference.
 */
function AuthGateToggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-success" : "bg-muted-foreground/30"
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  );
}

/** Ingress auth gate card (#851) with the Cognito pickers (#859). */
export function IngressAuthView({
  sourceKey,
  providerPluginSlug,
  ingressClass,
  existing,
  isAws,
  editing,
  poolId,
  onPoolIdChange,
  pools,
  poolsLoading,
  poolsErrored,
  poolsError,
  onRetryPools,
  clients,
  clientsLoading,
  clientsError,
  onRetryClients,
  busy,
  onOpenForm,
  onCancelEdit,
  onDisable,
  onApply,
  onSaveAndApply,
}: IngressAuthViewProps) {
  const t = useTranslations("clusterSettings.ingressAuth");
  const enabled = existing !== null;
  const [reviewedSource, setReviewedSource] = React.useState(sourceKey);

  const [poolArn, setPoolArn] = React.useState(existing?.user_pool_arn ?? "");
  const [clientId, setClientId] = React.useState(existing?.user_pool_client_id ?? "");
  const [domain, setDomain] = React.useState(existing?.user_pool_domain ?? "");
  // "Advanced / paste directly" fallback. AWS-only pickers degrade to
  // the original free-text inputs when the operator wants to paste a
  // cross-account pool the cluster's IAM role can't enumerate, or when
  // the Cognito list query errors.
  const [useAdvanced, setUseAdvanced] = React.useState(false);
  if (reviewedSource !== sourceKey) {
    setReviewedSource(sourceKey);
    setPoolArn(existing?.user_pool_arn ?? "");
    setClientId(existing?.user_pool_client_id ?? "");
    setDomain(existing?.user_pool_domain ?? "");
    setUseAdvanced(false);
  }

  function configFromFields(): IngressAuthConfig | null {
    if (poolArn && clientId && domain) {
      return { user_pool_arn: poolArn, user_pool_client_id: clientId, user_pool_domain: domain };
    }
    return null;
  }

  function openForm() {
    setPoolArn(existing?.user_pool_arn ?? "");
    setClientId(existing?.user_pool_client_id ?? "");
    setDomain(existing?.user_pool_domain ?? "");
    setUseAdvanced(false);
    onOpenForm();
  }

  // Operator picked a pool from the combobox: fill the ARN + pool id,
  // auto-fill the hosted domain (the whole point of #859 — no more
  // hand-typing it), and reset the app-client selection so the
  // dependent picker re-queries against the new pool.
  function pickPool(pool: CognitoUserPool) {
    setPoolArn(pool.poolArn);
    onPoolIdChange(pool.poolId);
    setDomain(pool.domain ?? "");
    setClientId("");
  }

  async function handleToggle() {
    if (busy) return;
    if (enabled) {
      await onDisable();
    } else {
      // Can't enable without config — open the form to collect it.
      openForm();
    }
  }

  async function handleApply() {
    if (busy) return;
    await onApply();
  }

  async function handleSaveAndApply() {
    if (busy) return;
    await onSaveAndApply(configFromFields());
  }

  const providerAuthMeta: Record<
    string,
    { supported: boolean; label: string; description: string; comingSoon?: string }
  > = {
    aws: {
      supported: ingressClass === "alb",
      label: t("awsLabel"),
      description: t("awsDescription"),
      comingSoon: ingressClass !== "alb" ? t("requiresAlb") : undefined,
    },
    gcp: {
      supported: false,
      label: "Google IAP",
      description: t("gcpDescription"),
      comingSoon: t("gcpUnsupported"),
    },
    azure: {
      supported: false,
      label: "Azure AD",
      description: t("azureDescription"),
      comingSoon: t("azureUnsupported"),
    },
    k8s_native: {
      supported: false,
      label: "OIDC (oauth2-proxy)",
      description: t("nativeDescription"),
      comingSoon: t("nativeUnsupported"),
    },
  };
  const authMeta = providerAuthMeta[providerPluginSlug] ?? {
    supported: false,
    label: t("genericLabel"),
    description: t("genericDescription"),
    comingSoon: t("providerUnsupported", { provider: providerPluginSlug }),
  };

  if (!authMeta.supported) {
    return (
      <Section title={authMeta.label} description={authMeta.description} divided>
        <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
          {authMeta.comingSoon}
        </p>
      </Section>
    );
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          {enabled ? (
            <ShieldIcon className="text-success-fg size-4 shrink-0" />
          ) : (
            <KeyRoundIcon className="size-4 shrink-0" />
          )}
          {authMeta.label}
        </span>
      }
      description={authMeta.description}
      action={
        <div className="flex shrink-0 items-center gap-2">
          <AuthGateToggle
            checked={enabled}
            onChange={handleToggle}
            disabled={busy}
            label={t(enabled ? "disable" : "enable")}
          />
          <span
            className={cn(
              "text-xs font-medium",
              enabled ? "text-success-fg" : "text-muted-foreground"
            )}
          >
            {t(enabled ? "configured" : "unconfigured")}
          </span>
        </div>
      }
      divided
    >
      <div className="min-w-0">
        <p className="text-muted-foreground mb-3 text-xs">{t("rolloutNotice")}</p>
        {editing ? (
          <div className="space-y-3">
            <p className="text-muted-foreground text-xs">{t("sourceLimit")}</p>
            {isAws && !useAdvanced ? (
              <>
                <div className="space-y-1">
                  <label className="text-xs font-medium">{t("pool")}</label>
                  <CognitoPoolCombobox
                    pools={pools}
                    loading={poolsLoading}
                    errored={poolsErrored}
                    error={poolsError}
                    onRetry={onRetryPools}
                    poolArn={poolArn}
                    onPick={pickPool}
                    onFreeText={(v) => {
                      setPoolArn(v);
                      onPoolIdChange(poolIdFromArn(v));
                    }}
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">{t("client")}</label>
                  <CognitoClientCombobox
                    clients={clients}
                    loading={clientsLoading}
                    unavailable={!!clientsError}
                    disabled={!poolId}
                    clientId={clientId}
                    onPick={setClientId}
                    onFreeText={setClientId}
                  />
                  {clientsError && (
                    <div role="alert" className="text-muted-foreground text-xs">
                      <p>{t("clientsUnavailable")}</p>
                      <p className="font-mono [overflow-wrap:anywhere]">{clientsError}</p>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={onRetryClients}
                        disabled={clientsLoading || busy}
                      >
                        {t("retry")}
                      </Button>
                    </div>
                  )}
                  {!poolId && <p className="text-muted-foreground text-xs">{t("poolFirst")}</p>}
                </div>
              </>
            ) : (
              <>
                <div className="space-y-1">
                  <label className="text-xs font-medium">{t("poolArn")}</label>
                  <Input
                    value={poolArn}
                    onChange={(e) => {
                      setPoolArn(e.target.value);
                      onPoolIdChange(poolIdFromArn(e.target.value));
                    }}
                    aria-label={t("poolArn")}
                    placeholder="arn:aws:cognito-idp:REGION:ACCOUNT:userpool/POOL"
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium">{t("clientId")}</label>
                  <Input
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    aria-label={t("clientId")}
                    placeholder="abc123…"
                    className="font-mono text-xs"
                  />
                </div>
              </>
            )}
            <div className="space-y-1">
              <label className="text-xs font-medium">{t("poolDomain")}</label>
              <Input
                value={domain}
                onChange={(e) => setDomain(e.target.value)}
                aria-label={t("poolDomain")}
                placeholder={t("domainPlaceholder")}
                className="font-mono text-xs"
              />
              {isAws && !useAdvanced && (
                <p className="text-muted-foreground text-xs">{t("domainHint")}</p>
              )}
            </div>
            {isAws && (
              <button
                type="button"
                onClick={() => setUseAdvanced((v) => !v)}
                className="text-muted-foreground hover:text-foreground text-xs underline-offset-4 hover:underline"
              >
                {useAdvanced ? t("picker") : t("advanced")}
              </button>
            )}
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={handleSaveAndApply} disabled={busy} className="gap-1.5">
                {busy && <Loader2Icon className="size-3.5 animate-spin" />}
                {t("saveApply")}
              </Button>
              <Button size="sm" variant="ghost" onClick={onCancelEdit} disabled={busy}>
                {t("cancel")}
              </Button>
            </div>
          </div>
        ) : enabled ? (
          <div className="space-y-3">
            <dl className="min-w-0 space-y-2 text-sm">
              <Field label={t("poolArn")} mono value={existing.user_pool_arn} />
              <Field label={t("clientId")} mono value={existing.user_pool_client_id} />
              <Field label={t("domain")} mono value={existing.user_pool_domain} />
            </dl>
            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={handleApply} disabled={busy} className="gap-1.5">
                {busy && <Loader2Icon className="size-3.5 animate-spin" />}
                <RocketIcon className="size-3.5" />
                {t("apply")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={openForm}
                disabled={busy}
                className="gap-1.5"
              >
                <PencilIcon className="size-3.5" />
                {t("edit")}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="border-warning-border bg-warning/10 flex items-start gap-2 rounded-md border p-3">
              <AlertTriangleIcon className="text-warning-fg mt-0.5 size-4 shrink-0" />
              <p className="text-warning-fg text-sm">{t("noConfig")}</p>
            </div>
            <Button size="sm" onClick={openForm} disabled={busy} className="gap-1.5">
              <ShieldIcon className="size-3.5" />
              {t("enable")}
            </Button>
          </div>
        )}
      </div>
    </Section>
  );
}

// User-pool picker (#859) — searchable combobox over the cluster's
// Cognito pools with free-text fallback. The displayed value is the
// pool ARN; picking a pool fills the ARN + pool id + auto-fills the
// domain (via onPick), while typing commits a raw ARN (via onFreeText)
// for cross-account pools the cluster's IAM role can't enumerate.
function CognitoPoolCombobox({
  pools,
  loading,
  errored,
  error,
  onRetry,
  poolArn,
  onPick,
  onFreeText,
}: {
  pools: CognitoUserPool[];
  loading: boolean;
  errored: boolean;
  error: string | null;
  onRetry: () => Promise<void>;
  poolArn: string;
  onPick: (pool: CognitoUserPool) => void;
  onFreeText: (v: string) => void;
}) {
  const t = useTranslations("clusterSettings.ingressAuth");
  const selected = pools.find((p) => p.poolArn === poolArn) ?? null;
  return (
    <>
      <Combobox<CognitoUserPool>
        items={pools}
        itemToStringLabel={(p) => p.poolArn}
        value={selected}
        onValueChange={(v) => {
          if (v && typeof v === "object" && "poolArn" in v) {
            onPick(v);
          }
        }}
        inputValue={poolArn}
        onInputValueChange={(v) => onFreeText(v ?? "")}
      >
        <ComboboxInput
          aria-label={t("poolArn")}
          placeholder={
            loading ? t("loadingPools") : "arn:aws:cognito-idp:REGION:ACCOUNT:userpool/POOL"
          }
          className="font-mono text-xs"
        />
        <ComboboxContent>
          <ComboboxEmpty>
            {poolArn
              ? t("pastePool", { value: poolArn })
              : t(errored ? "poolsUnavailable" : loading ? "loadingPools" : "noPools")}
          </ComboboxEmpty>
          <ComboboxList>
            {(item: CognitoUserPool) => (
              <ComboboxItem key={item.poolId} value={item}>
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm">{item.name || item.poolId}</span>
                  <span className="text-muted-foreground text-2xs truncate font-mono">
                    {item.poolId}
                    {item.domain ? ` · ${item.domain}` : ""}
                  </span>
                </div>
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
      {errored && (
        <div role="alert" className="text-muted-foreground text-xs">
          <p>{t("poolsUnavailable")}</p>
          {error && <p className="font-mono [overflow-wrap:anywhere]">{error}</p>}
          <Button size="sm" variant="ghost" onClick={onRetry} disabled={loading}>
            {t("retry")}
          </Button>
        </div>
      )}
    </>
  );
}

// Dependent app-client picker (#859) — enabled once a pool is selected.
// Same free-text fallback shape as the pool picker.
function CognitoClientCombobox({
  clients,
  loading,
  disabled,
  unavailable,
  clientId,
  onPick,
  onFreeText,
}: {
  clients: CognitoUserPoolClient[];
  loading: boolean;
  disabled: boolean;
  unavailable: boolean;
  clientId: string;
  onPick: (v: string) => void;
  onFreeText: (v: string) => void;
}) {
  const t = useTranslations("clusterSettings.ingressAuth");
  const selected = clients.find((c) => c.clientId === clientId) ?? null;
  return (
    <Combobox<CognitoUserPoolClient>
      items={clients}
      itemToStringLabel={(c) => c.clientId}
      value={selected}
      onValueChange={(v) => {
        if (v && typeof v === "object" && "clientId" in v) {
          onPick(v.clientId);
        }
      }}
      inputValue={clientId}
      onInputValueChange={(v) => onFreeText(v ?? "")}
      disabled={disabled}
    >
      <ComboboxInput
        aria-label={t("clientId")}
        placeholder={loading ? t("loadingClients") : "abc123…"}
        className="font-mono text-xs"
        disabled={disabled}
      />
      <ComboboxContent>
        <ComboboxEmpty>
          {clientId
            ? t("pasteClient", { value: clientId })
            : t(unavailable ? "clientsUnavailable" : loading ? "loadingClients" : "noClients")}
        </ComboboxEmpty>
        <ComboboxList>
          {(item: CognitoUserPoolClient) => (
            <ComboboxItem key={item.clientId} value={item}>
              <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <span className="truncate text-sm">{item.clientName || item.clientId}</span>
                <span className="text-muted-foreground text-2xs truncate font-mono">
                  {item.clientId}
                </span>
              </div>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
