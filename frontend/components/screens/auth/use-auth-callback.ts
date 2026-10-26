"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { signInReturnPath } from "@/lib/auth/sign-in-return";
import { setClientToken } from "@/lib/auth/token-store";

const apiRoot = process.env.NEXT_PUBLIC_API_ROOT ?? "";

/** The callback's `error=` values the login page explains. */
const SIGN_IN_ERRORS = new Set(["no_account", "email_unverified"]);

/** One trade per code: a code works once, and StrictMode runs effects twice. */
const trades = new Map<string, Promise<string | null>>();

/**
 * `POST /app/auth1/handoff`: the session token for the one-time code the
 * backend's callback put on this page's URL. The code is bound to this
 * browser's session cookie, so the request sends the cookie and no token, and
 * the session key itself never travels in a URL (#2429).
 */
function tradeSignInCode(code: string): Promise<string | null> {
  let trade = trades.get(code);
  if (!trade) {
    trade = fetch(`${apiRoot}/app/auth1/handoff`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-Platform": "web",
      },
      body: JSON.stringify({ code }),
    })
      .then(async (res) => {
        if (!res.ok) return null;
        const data = (await res.json()) as { Authorization?: unknown };
        return typeof data.Authorization === "string" ? data.Authorization : null;
      })
      .catch(() => null);
    trades.set(code, trade);
  }
  return trade;
}

/**
 * Finishes the IdP round-trip: trades the one-time code for the session
 * token, stores it (localStorage for client Apollo, httpOnly cookie for server
 * reads), then goes to the return path. No code, or one the backend refuses,
 * bounces to login, keeping the return path and saying why when the backend
 * did (an identity with no account, an unverified address). Reads search
 * params, so its caller sits under a Suspense boundary.
 */
export function useAuthCallback() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    let current = true;
    const code = searchParams.get("code");
    const returnPath = signInReturnPath(searchParams.get("next"));

    function toLogin(error: string | null) {
      const login = new URLSearchParams();
      if (error && SIGN_IN_ERRORS.has(error)) login.set("error", error);
      if (returnPath !== "/dashboard") login.set("next", returnPath);
      const query = login.toString();
      router.replace(query ? `/auth/login?${query}` : "/auth/login");
    }

    if (!code) {
      toLogin(searchParams.get("error"));
      return;
    }

    tradeSignInCode(code).then((token) => {
      if (!current) return;
      if (!token) {
        toLogin(null);
        return;
      }
      // Store in localStorage for client-side Apollo
      setClientToken(token);

      // Store in httpOnly cookie for server-side token reads
      fetch("/api/auth/store-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      }).finally(() => {
        if (returnPath === "/dashboard") router.replace(returnPath);
        else window.location.replace(returnPath);
      });
    });
    return () => {
      current = false;
    };
  }, [router, searchParams]);
}
