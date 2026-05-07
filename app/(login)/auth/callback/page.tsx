"use client";

import Image from "next/image";
import { useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function BrandFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-4">
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="text-foreground text-lg font-semibold tracking-tight">
        Astrolift
      </div>
      <div className="text-muted-foreground text-sm">{children}</div>
    </div>
  );
}

function CallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const token = searchParams.get("token");
    if (!token) {
      router.replace("/auth/login");
      return;
    }

    // Store in localStorage for client-side Apollo
    localStorage.setItem("jwt", token);

    // Store in httpOnly cookie for server-side token reads
    fetch("/api/auth/store-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    }).finally(() => {
      router.replace("/dashboard");
    });
  }, [router, searchParams]);

  return <BrandFrame>Completing login…</BrandFrame>;
}

export default function CallbackPage() {
  return (
    <Suspense fallback={<BrandFrame>Loading…</BrandFrame>}>
      <CallbackInner />
    </Suspense>
  );
}
