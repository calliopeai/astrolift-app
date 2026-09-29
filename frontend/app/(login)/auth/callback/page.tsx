"use client";

import { Suspense } from "react";

import { AuthSplash } from "@/components/screens/auth/AuthSplash";
import { useAuthCallback } from "@/components/screens/auth/use-auth-callback";

function CallbackInner() {
  useAuthCallback();
  return <AuthSplash message="Completing login…" />;
}

export default function CallbackPage() {
  return (
    <Suspense fallback={<AuthSplash message="Loading…" />}>
      <CallbackInner />
    </Suspense>
  );
}
