"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

// In dev (NEXT_PUBLIC_DEV_LOGIN=1) we honour the legacy dev-login
// bypass and skip everything else. In any other build we read the
// active IdP from /app/auth1/active-idp.json and render the right
// CTA — Auth0 / Cognito / OIDC button, or a username+password form
// when kind=local.
const useDevLogin = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

interface ActiveIdp {
  kind: string | null;
  displayName: string;
  loginPath: string | null;
  ready: boolean;
  hint?: string;
}

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

export default function LoginPage() {
  const router = useRouter();
  const [idp, setIdp] = React.useState<ActiveIdp | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const apiRoot = process.env.NEXT_PUBLIC_API_ROOT ?? "";

  React.useEffect(() => {
    if (useDevLogin) {
      window.location.href = `${apiRoot}/app/auth1/dev-login?next=/dashboard`;
      return;
    }
    fetch(`${apiRoot}/app/auth1/active-idp.json`, { credentials: "include" })
      .then((r) => r.json())
      .then(setIdp)
      .catch((err) => setLoadError(String(err)));
  }, [apiRoot]);

  if (useDevLogin) {
    return <Splash message="Redirecting to dev login…" />;
  }

  if (loadError) {
    return (
      <Splash
        message="Couldn't reach the API"
        detail={loadError}
        tone="destructive"
      />
    );
  }

  if (idp === null) {
    return <Splash message="Detecting identity provider…" />;
  }

  if (!idp.ready) {
    return (
      <Splash
        message="No identity provider configured"
        detail={
          idp.hint ??
          "An operator must configure an IdP before users can log in."
        }
        tone="warning"
      />
    );
  }

  if (idp.kind === "local") {
    return <LocalLoginForm next="/dashboard" />;
  }

  // External IdP: render a single CTA that kicks off the auth1 round-trip.
  return (
    <ExternalIdpCard
      kind={idp.kind ?? "external"}
      displayName={idp.displayName}
      loginPath={idp.loginPath ?? "/app/auth1/login"}
      apiRoot={apiRoot}
    />
  );
}

function ExternalIdpCard({
  kind,
  displayName,
  loginPath,
  apiRoot,
}: {
  kind: string;
  displayName: string;
  loginPath: string;
  apiRoot: string;
}) {
  const ctaLabel = `Continue with ${
    DISPLAY_KIND[kind] ?? displayName ?? "your provider"
  }`;
  function go() {
    const callback = encodeURIComponent(`${window.location.origin}/auth/callback`);
    window.location.href = `${apiRoot}${loginPath}?next=${callback}`;
  }
  return (
    <div className="flex flex-col items-center gap-6">
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="flex flex-col items-center gap-1">
        <div className="text-foreground text-xl font-semibold tracking-tight">
          Sign in to Astrolift
        </div>
        <div className="text-muted-foreground text-sm">
          via {displayName}
        </div>
      </div>
      <Button onClick={go} size="lg" className="w-72">
        {ctaLabel}
      </Button>
    </div>
  );
}

function LocalLoginForm({ next }: { next: string }) {
  const router = useRouter();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const apiRoot = process.env.NEXT_PUBLIC_API_ROOT ?? "";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await fetch(`${apiRoot}/app/auth1/local-login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        credentials: "include",
        body: JSON.stringify({ username, password, next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        toast.error(body.detail ?? `Login failed (${res.status})`);
        return;
      }
      router.push(next);
    } catch (err) {
      toast.error(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex w-full max-w-sm flex-col items-center gap-6"
    >
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="flex flex-col items-center gap-1">
        <div className="text-foreground text-xl font-semibold tracking-tight">
          Sign in to Astrolift
        </div>
        <div className="text-muted-foreground text-sm">
          Local username + password
        </div>
      </div>

      <div className="w-full space-y-2">
        <Label htmlFor="username">Username or email</Label>
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
        <Label htmlFor="password">Password</Label>
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
        {busy ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

function Splash({
  message,
  detail,
  tone = "default",
}: {
  message: string;
  detail?: string;
  tone?: "default" | "warning" | "destructive";
}) {
  const toneClass =
    tone === "destructive"
      ? "text-destructive"
      : tone === "warning"
        ? "text-amber-600 dark:text-amber-400"
        : "text-muted-foreground";
  return (
    <div className="flex flex-col items-center gap-4">
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="text-foreground text-lg font-semibold tracking-tight">
        Astrolift
      </div>
      <div className={`${toneClass} text-sm`}>{message}</div>
      {detail && (
        <div className="text-muted-foreground max-w-md text-center text-xs">
          {detail}
        </div>
      )}
    </div>
  );
}
