import { useTranslations } from "next-intl";
import * as React from "react";

import { PageShell } from "@/components/PageShell";

/**
 * Settings › Security. The cards arrive as slots: each one fetches its own
 * data (the sessions list is preloaded by the route).
 */
export function SecurityScreen({
  sessions,
  pairDevice,
}: {
  sessions: React.ReactNode;
  pairDevice: React.ReactNode;
}) {
  const t = useTranslations("securitySettings");
  return (
    <PageShell title={t("title")} description={t("description")}>
      <div className="grid gap-4">
        {sessions}
        {pairDevice}
      </div>
    </PageShell>
  );
}
