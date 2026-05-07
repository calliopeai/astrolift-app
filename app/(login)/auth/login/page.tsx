"use client";

import Image from "next/image";
import { useEffect } from "react";

// In dev (NEXT_PUBLIC_DEV_LOGIN=1) hit the backend's dev-login bypass
// so we don't need a working Auth0 tenant to click through the UI.
// In any other build we use the real Auth0 round-trip.
const useDevLogin = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

export default function LoginPage() {
  useEffect(() => {
    const apiRoot = process.env.NEXT_PUBLIC_API_ROOT ?? "";
    if (useDevLogin) {
      window.location.href = `${apiRoot}/app/auth1/dev-login?next=/dashboard`;
      return;
    }
    const next = encodeURIComponent(`${window.location.origin}/auth/callback`);
    window.location.href = `${apiRoot}/app/auth1/login?next=${next}`;
  }, []);

  return (
    <div className="flex flex-col items-center gap-4">
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="text-foreground text-lg font-semibold tracking-tight">
        Astrolift
      </div>
      <div className="text-muted-foreground text-sm">Redirecting to login…</div>
    </div>
  );
}
