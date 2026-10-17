"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { signInReturnPath } from "@/lib/auth/sign-in-return";

/**
 * Finishes the IdP round-trip: stores the token (localStorage for client
 * Apollo, httpOnly cookie for server reads), then goes to the return path.
 * No token bounces to login, keeping the return path and saying why when the
 * backend did (an identity with no account). Reads search params, so its
 * caller sits under a Suspense boundary.
 */
export function useAuthCallback() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const token = searchParams.get("token");
    const returnPath = signInReturnPath(searchParams.get("next"));
    if (!token) {
      const login = new URLSearchParams();
      if (searchParams.get("error") === "no_account") login.set("error", "no_account");
      if (returnPath !== "/dashboard") login.set("next", returnPath);
      const query = login.toString();
      router.replace(query ? `/auth/login?${query}` : "/auth/login");
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
      if (returnPath === "/dashboard") router.replace(returnPath);
      else window.location.replace(returnPath);
    });
  }, [router, searchParams]);
}
