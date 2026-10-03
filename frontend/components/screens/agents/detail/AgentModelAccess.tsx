"use client";

import type * as React from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";

import type { useAgentModelAccess } from "./use-agent-model-access";

export type AgentModelAccessViewProps = Pick<
  ReturnType<typeof useAgentModelAccess>,
  "spec" | "loading" | "orgId"
> & {
  error?: string | { message: string } | null;
  onRetry?: () => void;
  /** The managed-model toggle for the resolved spec (`ManagedModelSection`). */
  managedModel?: React.ReactNode;
  /** The VNC-vs-headless toggle for the resolved spec (`VncSessionSection`). */
  vncSession?: React.ReactNode;
};

/**
 * Agent-context-only "Model access" and "Live session" sections for
 * `/agents/[agentSlug]/settings`. The two toggles are slots, rendered once the
 * agent's environment spec has resolved.
 */
export function AgentModelAccessView({
  spec,
  loading,
  orgId,
  error,
  onRetry,
  managedModel,
  vncSession,
}: AgentModelAccessViewProps) {
  const t = useTranslations("agentModelAccess");
  const fallback = error ? null : loading || !orgId ? (
    <Skeleton className="h-20 w-full" />
  ) : (
    <p className="text-muted-foreground text-sm italic">{t("noSpec")}</p>
  );

  return (
    <>
      {error && (
        <div role="alert" className="space-y-2 rounded-md border p-3 text-sm">
          <p>{t("readFailed")}</p>
          <p className="font-mono text-xs break-all">
            {typeof error === "string" ? error : error.message}
          </p>
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              {t("retry")}
            </Button>
          )}
        </div>
      )}
      <Section title={t("title")} description={t("description")}>
        {spec ? managedModel : fallback}
      </Section>
      <Section title={t("liveTitle")} description={t("liveDescription")}>
        {spec ? vncSession : fallback}
      </Section>
    </>
  );
}
