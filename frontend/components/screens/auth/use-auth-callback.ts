"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { deviceApprovalReturnPath } from "@/lib/auth/device-return";

/**
 * Finishes the IdP round-trip: stores the token (localStorage for client
 * Apollo, httpOnly cookie for server reads), then goes to the return path.
 * No token bounces to login. Reads search params, so its caller sits under
 * a Suspense boundary.
 */
export function useAuthCallback() {
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
      const returnPath = deviceApprovalReturnPath(searchParams.get("next"));
      if (returnPath === "/dashboard") router.replace(returnPath);
      else window.location.replace(returnPath);
    });
  }, [router, searchParams]);
}
