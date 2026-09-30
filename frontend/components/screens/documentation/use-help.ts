"use client";

import * as React from "react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";

/** Active org plus the copy-diagnostics action for the Get help screen. */
export function useHelp(platformVersion: string) {
  const { org } = useActiveOrg();
  const [copied, setCopied] = React.useState(false);
  const copiedTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = React.useRef(true);
  React.useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (copiedTimer.current !== null) clearTimeout(copiedTimer.current);
    };
  }, []);

  async function copyDiagnostics() {
    if (typeof navigator === "undefined") return;
    const diagnostics = {
      platform_version: platformVersion,
      browser_user_agent: navigator.userAgent || "unknown",
      current_org_slug: org?.slug ?? null,
      current_org_name: org?.name ?? null,
      current_url: window.location.href,
      timestamp: new Date().toISOString(),
    };
    const text = JSON.stringify(diagnostics, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      if (!mounted.current) return;
      if (copiedTimer.current !== null) clearTimeout(copiedTimer.current);
      setCopied(true);
      toast.success("Diagnostic info copied — paste into your ticket");
      copiedTimer.current = setTimeout(() => {
        copiedTimer.current = null;
        setCopied(false);
      }, 1500);
    } catch {
      if (mounted.current) toast.error("Couldn't copy — clipboard access blocked");
    }
  }

  return { org, copied, copyDiagnostics };
}
