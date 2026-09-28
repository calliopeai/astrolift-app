"use client";

import * as React from "react";
import { toast } from "sonner";

import { useActiveOrg } from "@/graphql/identity/identity.hooks";

/** Active org plus the copy-diagnostics action for the Get help screen. */
export function useHelp(platformVersion: string) {
  const { org } = useActiveOrg();
  const [copied, setCopied] = React.useState(false);

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
      setCopied(true);
      toast.success("Diagnostic info copied — paste into your ticket");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy — clipboard access blocked");
    }
  }

  return { org, copied, copyDiagnostics };
}
