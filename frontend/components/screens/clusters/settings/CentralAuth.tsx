"use client";

import { KeyRoundIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";
import type { CentralAuthDraft } from "./types";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { SettingsSection } from "@/components/settings/SettingsPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import type { useCentralAuth, useIngressClass } from "./use-central-auth";

/**
 * The cluster's central auth (``oidcAuthConfig``) and the ingress class
 * that decides which gate is in force (#2119).
 *
 * Both were reachable only through ``updateTenantCluster`` over GraphQL, so
 * turning central auth on took a hand-minted admin token. Secrets are
 * write-only: the server reports whether each is set, a blank field keeps the
 * stored value, and nothing here ever holds one it did not just type.
 */

function SecretBadge({ label, set }: { label: string; set?: boolean }) {
  const t = useTranslations("clusterSettings.centralAuth");
  return (
    <Badge variant={set ? "secondary" : "outline"} className="gap-1">
      <KeyRoundIcon className="size-3" />
      {t("secretBadge", {
        label,
        state:
          set === true ? t("secretSet") : set === false ? t("secretNotSet") : t("secretUnknown"),
      })}
    </Badge>
  );
}

export type CentralAuthViewProps = ReturnType<typeof useCentralAuth>;

export function CentralAuthView({ clusterId, view, saving, onSave }: CentralAuthViewProps) {
  const t = useTranslations("clusterSettings.centralAuth");
  function initialDraft(): CentralAuthDraft {
    return {
      discoveryUrl: view?.discovery_url ?? "",
      clientId: view?.client_id ?? "",
      authHost: view?.auth_proxy_host ?? "",
      jwksUri: view?.jwks_uri ?? "",
      clientSecret: "",
    };
  }
  const [form, setForm] = React.useState({
    clusterId,
    configured: view !== null,
    epoch: 0,
    editing: false,
    draft: initialDraft(),
  });
  if (form.clusterId !== clusterId || form.configured !== (view !== null)) {
    setForm({
      clusterId,
      configured: view !== null,
      epoch: form.epoch + 1,
      editing: false,
      draft: initialDraft(),
    });
  }
  const {
    editing,
    draft: { discoveryUrl, clientId, authHost, jwksUri, clientSecret },
  } = form;
  function setField(field: keyof CentralAuthDraft, value: string) {
    setForm((current) => ({ ...current, draft: { ...current.draft, [field]: value } }));
  }
  function reset(editing: boolean) {
    setForm((current) => ({ ...current, editing, draft: initialDraft() }));
  }
  async function save() {
    const epoch = form.epoch;
    if (await onSave(form.draft)) {
      setForm((current) =>
        current.clusterId === clusterId && current.epoch === epoch
          ? { ...current, editing: false, draft: { ...current.draft, clientSecret: "" } }
          : current
      );
    }
  }
  const description = t.rich("description", {
    callbackUrl: "https://<auth host>/oauth2/callback",
    callback: (chunks) => (
      <code className="font-mono text-xs [overflow-wrap:anywhere]">{chunks}</code>
    ),
  });

  if (editing) {
    return (
      // An open edit counts as unsaved: Save and Cancel are live from the
      // start, and `required` holds back a save with a blank field.
      <SettingsSection
        title={t("title")}
        description={description}
        dirty
        saving={saving}
        onSave={save}
        onCancel={() => reset(false)}
      >
        <TextField
          id="oidc-auth-host"
          label={t("authHost")}
          value={authHost}
          onChange={(value) => setField("authHost", value)}
          placeholder="auth.apps.example.com"
          required
        />
        <TextField
          id="oidc-discovery"
          label={t("discoveryUrl")}
          value={discoveryUrl}
          onChange={(value) => setField("discoveryUrl", value)}
          placeholder="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration"
          required
        />
        <TextField
          id="oidc-client-id"
          label={t("clientId")}
          value={clientId}
          onChange={(value) => setField("clientId", value)}
          required
        />
        <TextField
          id="oidc-client-secret"
          label={t("clientSecret")}
          value={clientSecret}
          onChange={(value) => setField("clientSecret", value)}
          type="password"
          autoComplete="new-password"
          placeholder={
            view?.client_secret_set === true
              ? t("secretKeep")
              : view?.client_secret_set === false
                ? t("notSet")
                : t("secretUnknown")
          }
          hint={t("writeOnly")}
        />
        <TextField
          id="oidc-jwks"
          label={t("jwksOptional")}
          value={jwksUri}
          onChange={(value) => setField("jwksUri", value)}
          hint={t("jwksHint", { jwksPath: "<issuer>/.well-known/jwks.json" })}
        />
      </SettingsSection>
    );
  }

  return (
    <Section title={t("title")} description={description} divided>
      <div className="flex min-w-0 flex-col gap-4">
        {view ? (
          <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
            <ViewField label={t("authHost")} value={view.auth_proxy_host} />
            <ViewField label={t("clientId")} value={view.client_id} />
            <ViewField label={t("discoveryUrl")} value={view.discovery_url} />
            {view.jwks_uri && <ViewField label={t("jwksUri")} value={view.jwks_uri} />}
          </dl>
        ) : (
          <p className="text-muted-foreground text-sm">{t("notConfigured")}</p>
        )}
        {view && (
          <div className="flex flex-wrap gap-1.5">
            <SecretBadge label={t("clientSecret")} set={view.client_secret_set} />
            <SecretBadge label={t("cookieSecret")} set={view.cookie_secret_set} />
            <SecretBadge label={t("gatewaySecret")} set={view.gateway_secret_set} />
          </div>
        )}
        <div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              reset(true);
            }}
          >
            {view ? t("edit") : t("setup")}
          </Button>
        </div>
      </div>
    </Section>
  );
}

const CLASSES: { value: string; label: string; gate: string }[] = [
  {
    value: "envoy",
    label: "Envoy edge (central auth)",
    gate: "the central auth above, on one Envoy Gateway",
  },
  { value: "alb", label: "ALB (per-app Cognito)", gate: "the ALB Cognito gate" },
  {
    value: "nginx",
    label: "ingress-nginx (central auth)",
    gate: "the central auth above, through oauth2-proxy. ingress-nginx is past end of maintenance",
  },
];

export type IngressClassViewProps = ReturnType<typeof useIngressClass>;

/**
 * The class is the switch that moves apps between edges. The server refuses a
 * flip that would drop the gate (#1616); this says so before the click.
 */
export function IngressClassView({
  ingressClass,
  albGate,
  centralAuthConfigured,
  onApply,
}: IngressClassViewProps) {
  const [target, setTarget] = React.useState(ingressClass);
  const [syncManifests, setSyncManifests] = React.useState(false);
  const [confirming, setConfirming] = React.useState(false);

  const targetHasGate = target === "alb" ? albGate : centralAuthConfigured;
  const changing = target !== ingressClass;
  const chosen = CLASSES.find((c) => c.value === target);

  return (
    <>
      <SettingsSection
        title="Ingress class"
        description="Which edge serves this cluster's apps. Each app moves on its next deploy; running apps are not touched by the change itself."
        dirty={changing}
        saveLabel="Change class"
        onSave={() => setConfirming(true)}
        onCancel={() => {
          setTarget(ingressClass);
          setSyncManifests(false);
        }}
      >
        <Select value={target} onValueChange={setTarget}>
          <SelectTrigger className="w-full max-w-72" aria-label="Ingress class">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CLASSES.map((c) => (
              <SelectItem key={c.value} value={c.value}>
                {c.label}
              </SelectItem>
            ))}
            {!CLASSES.some((c) => c.value === ingressClass) && (
              <SelectItem value={ingressClass}>
                <span className="font-mono">{ingressClass}</span>
              </SelectItem>
            )}
          </SelectContent>
        </Select>
        {changing && chosen && (
          <p className="text-muted-foreground text-xs">Apps will be gated by {chosen.gate}.</p>
        )}
        {changing && !targetHasGate && (
          <p role="alert" className="text-warning-fg text-xs">
            {target === "alb"
              ? "No ALB Cognito gate is set, so apps on this cluster would be public."
              : "Central auth is not configured above, so apps on this cluster would be public."}{" "}
            The change is refused while the current class has a gate; set the new one first.
          </p>
        )}
        {changing && (
          <label className="flex min-w-0 items-start gap-2 text-xs">
            <Checkbox
              checked={syncManifests}
              onCheckedChange={(v) => setSyncManifests(v === true)}
              className="mt-0.5"
            />
            <span className="min-w-0">
              Also write the new gate into every app&apos;s{" "}
              <code className="font-mono">astrolift.toml</code>. This commits to each app&apos;s
              repo, and each commit starts that app&apos;s deploy, so every app redeploys at once.
            </span>
          </label>
        )}
      </SettingsSection>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={
          <>
            Change the ingress class to <span className="font-mono">{target}</span>?
          </>
        }
        description={
          syncManifests
            ? "Every app on this cluster gets a commit to its astrolift.toml and redeploys now."
            : "No app redeploys now. Each one moves to the new edge on its next deploy."
        }
        confirmLabel="Change class"
        onConfirm={() => onApply(target, syncManifests)}
      />
    </>
  );
}

function ViewField({ label, value }: { label: string; value?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="font-mono text-xs [overflow-wrap:anywhere]">{value || "—"}</dd>
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  hint,
  ...rest
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
} & Omit<React.ComponentProps<typeof Input>, "id" | "value" | "onChange">) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="font-mono"
        {...rest}
      />
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  );
}
