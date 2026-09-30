"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

export function AgentSecretReadError({
  label,
  message,
  onRetry,
}: {
  label: string;
  message: string;
  onRetry: () => void;
}) {
  const t = useTranslations("shared.table");
  return (
    <div role="alert" className="flex min-w-0 flex-col items-start gap-2 text-sm">
      <p className="font-medium">{t("loadFailed", { label })}</p>
      <p className="text-muted-foreground max-w-full [overflow-wrap:anywhere]">{message}</p>
      <Button size="sm" variant="outline" onClick={onRetry}>
        {t("retry")}
      </Button>
    </div>
  );
}
