"use client";

import { KeyRoundIcon, RouteIcon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  return (
    <Badge variant={set ? "secondary" : "outline"} className="gap-1">
      <KeyRoundIcon className="size-3" />
      {label}: {set ? "set" : "not set"}
    </Badge>
  );
}

export type CentralAuthViewProps = ReturnType<typeof useCentralAuth>;

export function CentralAuthView({ view, saving, onSave }: CentralAuthViewProps) {
  const [editing, setEditing] = React.useState(false);
  const [discoveryUrl, setDiscoveryUrl] = React.useState(view?.discovery_url ?? "");
  const [clientId, setClientId] = React.useState(view?.client_id ?? "");
  const [authHost, setAuthHost] = React.useState(view?.auth_proxy_host ?? "");
  const [jwksUri, setJwksUri] = React.useState(view?.jwks_uri ?? "");
  const [clientSecret, setClientSecret] = React.useState("");

  function reset() {
    setDiscoveryUrl(view?.discovery_url ?? "");
    setClientId(view?.client_id ?? "");
    setAuthHost(view?.auth_proxy_host ?? "");
    setJwksUri(view?.jwks_uri ?? "");
    setClientSecret("");
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (await onSave({ discoveryUrl, clientId, authHost, jwksUri, clientSecret })) {
      setClientSecret("");
      setEditing(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRoundIcon className="size-4" />
          Central auth
        </CardTitle>
        <CardDescription>
          One sign-in for every app on this cluster. The identity provider needs exactly one
          callback,{" "}
          <code className="font-mono text-xs">https://&lt;auth host&gt;/oauth2/callback</code>, and
          the session covers every host under the auth host&apos;s parent zone. Read by the Envoy
          and nginx edges; on the ALB class the per-app Cognito gate applies instead.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!editing ? (
          <>
            {view ? (
              <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
                <ViewField label="Auth host" value={view.auth_proxy_host} />
                <ViewField label="Client ID" value={view.client_id} />
                <ViewField label="Discovery URL" value={view.discovery_url} />
                {view.jwks_uri && <ViewField label="JWKS URI" value={view.jwks_uri} />}
              </dl>
            ) : (
              <p className="text-muted-foreground text-sm">Not configured.</p>
            )}
            {view && (
              <div className="flex flex-wrap gap-1.5">
                <SecretBadge label="Client secret" set={view.client_secret_set} />
                <SecretBadge label="Cookie secret" set={view.cookie_secret_set} />
                <SecretBadge label="Gateway secret" set={view.gateway_secret_set} />
              </div>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                reset();
                setEditing(true);
              }}
            >
              {view ? "Edit" : "Set up central auth"}
            </Button>
          </>
        ) : (
          <form onSubmit={save} className="space-y-3">
            <TextField
              id="oidc-auth-host"
              label="Auth host"
              value={authHost}
              onChange={setAuthHost}
              placeholder="auth.apps.example.com"
              required
            />
            <TextField
              id="oidc-discovery"
              label="Discovery URL"
              value={discoveryUrl}
              onChange={setDiscoveryUrl}
              placeholder="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration"
              required
            />
            <TextField
              id="oidc-client-id"
              label="Client ID"
              value={clientId}
              onChange={setClientId}
              required
            />
            <TextField
              id="oidc-client-secret"
              label="Client secret"
              value={clientSecret}
              onChange={setClientSecret}
              type="password"
              autoComplete="new-password"
              placeholder={view?.client_secret_set ? "Set. Leave empty to keep it." : "Not set"}
              hint="Write-only. Never shown again once saved."
            />
            <TextField
              id="oidc-jwks"
              label="JWKS URI (optional)"
              value={jwksUri}
              onChange={setJwksUri}
              hint="Only for a provider that does not serve its keys at <issuer>/.well-known/jwks.json."
            />
            <div className="flex justify-end gap-2">
              <Button type="button" variant="outline" size="sm" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={saving || !discoveryUrl.trim() || !clientId.trim() || !authHost.trim()}
              >
                {saving ? "Saving…" : "Save"}
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
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
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <RouteIcon className="size-4" />
          Ingress class
        </CardTitle>
        <CardDescription>
          Which edge serves this cluster&apos;s apps. Each app moves on its next deploy; running
          apps are not touched by the change itself.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <Select value={target} onValueChange={setTarget}>
            <SelectTrigger className="w-72" aria-label="Ingress class">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CLASSES.map((c) => (
                <SelectItem key={c.value} value={c.value}>
                  {c.label}
                </SelectItem>
              ))}
              {!CLASSES.some((c) => c.value === ingressClass) && (
                <SelectItem value={ingressClass}>{ingressClass}</SelectItem>
              )}
            </SelectContent>
          </Select>
          <Button size="sm" disabled={!changing} onClick={() => setConfirming(true)}>
            Change class
          </Button>
        </div>
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
          <label className="flex items-start gap-2 text-xs">
            <Checkbox
              checked={syncManifests}
              onCheckedChange={(v) => setSyncManifests(v === true)}
              className="mt-0.5"
            />
            <span>
              Also write the new gate into every app&apos;s <code>astrolift.toml</code>. This
              commits to each app&apos;s repo, and each commit starts that app&apos;s deploy, so
              every app redeploys at once.
            </span>
          </label>
        )}
      </CardContent>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Change the ingress class to ${target}?`}
        description={
          syncManifests
            ? "Every app on this cluster gets a commit to its astrolift.toml and redeploys now."
            : "No app redeploys now. Each one moves to the new edge on its next deploy."
        }
        confirmLabel="Change class"
        onConfirm={() => onApply(target, syncManifests)}
      />
    </Card>
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
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} {...rest} />
      {hint && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  );
}
