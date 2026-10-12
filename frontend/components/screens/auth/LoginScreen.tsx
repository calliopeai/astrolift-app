"use client";

import Image from "next/image";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { AuthSplash } from "./AuthSplash";
import type { useLogin } from "./use-login";

const DISPLAY_KIND: Record<string, string> = {
  auth0: "Auth0",
  oidc: "OpenID Connect",
  cognito: "Amazon Cognito",
  okta: "Okta",
  azure_ad: "Azure AD",
  google: "Google",
  github: "GitHub",
  saml: "SAML SSO",
  local: "username and password",
};

export type LoginScreenProps = ReturnType<typeof useLogin>;

/** The sign-in page: whichever step of IdP detection the page is on, then the right CTA. */
export function LoginScreen({
  devLogin,
  idp,
  loadError,
  continueWithIdp,
  submitLocal,
}: LoginScreenProps) {
  const t = useTranslations("loginScreen");
  if (devLogin) {
    return <AuthSplash message={t("redirectingDev")} />;
  }

  if (loadError) {
    return <AuthSplash message={t("apiUnreachable")} detail={loadError} tone="destructive" />;
  }

  if (idp === null) {
    return <AuthSplash message={t("detecting")} />;
  }

  if (!idp.ready) {
    return <AuthSplash message={t("noIdp")} detail={idp.hint ?? t("noIdpHint")} tone="warning" />;
  }

  if (idp.kind === "local") {
    return <LocalLoginForm onSubmit={submitLocal} />;
  }

  // External IdP: render a single CTA that kicks off the auth1 round-trip.
  return (
    <ExternalIdpCard
      kind={idp.kind ?? "external"}
      displayName={idp.displayName}
      onContinue={continueWithIdp}
    />
  );
}

export function ExternalIdpCard({
  kind,
  displayName,
  onContinue,
}: {
  kind: string;
  displayName: string;
  onContinue: () => void;
}) {
  const t = useTranslations("loginScreen");
  const ctaLabel = t("continueWith", {
    provider: DISPLAY_KIND[kind] ?? displayName ?? t("yourProvider"),
  });
  return (
    <div className="flex flex-col items-center gap-6">
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="flex flex-col items-center gap-1">
        <div className="text-foreground text-xl font-semibold tracking-tight">{t("title")}</div>
        <div className="text-muted-foreground text-sm">{t("via", { provider: displayName })}</div>
      </div>
      <Button onClick={onContinue} size="lg" className="w-72">
        {ctaLabel}
      </Button>
    </div>
  );
}

export function LocalLoginForm({
  onSubmit,
}: {
  onSubmit: (username: string, password: string) => Promise<void>;
}) {
  const t = useTranslations("loginScreen");
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await onSubmit(username, password);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex w-full max-w-sm flex-col items-center gap-6">
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="flex flex-col items-center gap-1">
        <div className="text-foreground text-xl font-semibold tracking-tight">{t("title")}</div>
        <div className="text-muted-foreground text-sm">{t("localSubtitle")}</div>
      </div>

      <div className="w-full space-y-2">
        <Label htmlFor="username">{t("username")}</Label>
        <Input
          id="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
          required
        />
      </div>
      <div className="w-full space-y-2">
        <Label htmlFor="password">{t("password")}</Label>
        <Input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </div>

      <Button type="submit" size="lg" className="w-full" disabled={busy}>
        {busy ? t("signingIn") : t("signIn")}
      </Button>
    </form>
  );
}
