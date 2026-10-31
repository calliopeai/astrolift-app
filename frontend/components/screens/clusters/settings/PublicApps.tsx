"use client";

import { GlobeIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";

import type { usePublicApps } from "./use-central-auth";

export type PublicAppsViewProps = ReturnType<typeof usePublicApps>;

/**
 * Whether an app on this cluster may skip the edge login when its owner
 * asks (#2504). On by default; each app is still behind the login until
 * someone turns its own switch on. Off gates every app on the cluster again
 * at once. Only the Envoy edge honours an app's request.
 */
export function PublicAppsView({
  clusterId,
  allowed,
  envoy,
  saving,
  onApply,
}: PublicAppsViewProps) {
  const t = useTranslations("clusterSettings.publicApps");
  const [review, setReview] = React.useState({ clusterId, allowed, confirming: false });
  if (review.clusterId !== clusterId || review.allowed !== allowed) {
    setReview({ clusterId, allowed, confirming: false });
  }
  return (
    <Section title={t("title")} description={t("description")} divided>
      <div className="flex min-w-0 flex-col items-start gap-3">
        <div className="flex items-center gap-2 text-sm">
          <GlobeIcon className="size-4" />
          <Badge variant={allowed ? "outline" : "secondary"}>
            {allowed ? t("allowed") : t("refused")}
          </Badge>
        </div>
        {!envoy && <p className="text-muted-foreground text-xs">{t("notEnvoy")}</p>}
        <Button
          size="sm"
          variant="outline"
          disabled={saving}
          onClick={() => setReview((current) => ({ ...current, confirming: true }))}
        >
          {allowed ? t("refuse") : t("allow")}
        </Button>
      </div>
      <ConfirmDialog
        open={review.confirming}
        onOpenChange={(confirming) => setReview((current) => ({ ...current, confirming }))}
        title={allowed ? t("refuseTitle") : t("allowTitle")}
        description={allowed ? t("refuseReview") : t("allowReview")}
        confirmLabel={allowed ? t("refuse") : t("allow")}
        destructive={!allowed}
        onConfirm={() => onApply(!allowed)}
      />
    </Section>
  );
}
