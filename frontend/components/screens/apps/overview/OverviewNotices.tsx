"use client";

/**
 * The overview's one notices slot (spec 44 §5.2). Autowire, drift, a pending
 * deregister, reprovision, the GitHub link and provisioning progress used to
 * stack as competing coloured banners above the page. They now sit as quiet
 * rows in one bordered frame above the panel grid: the tone is the icon's
 * colour only, the text stays neutral, and the frame disappears when every
 * notice has nothing to say.
 *
 *   <OverviewNotices>
 *     <AutowireStatusBanner … />   // each renders a <Notice> or null
 *     <ConfigDriftBanner … />
 *   </OverviewNotices>
 *
 * A `Notice` outside the slot (the Settings tab's deregister banner) draws a
 * border of its own.
 */

import { useTranslations } from "next-intl";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

export type NoticeTone = "info" | "warning" | "danger" | "progress";

const TONE_ICON: Record<NoticeTone, string> = {
  info: "text-info-fg",
  warning: "text-warning-fg",
  danger: "text-danger-fg",
  progress: "text-primary",
};

const GroupedContext = React.createContext(false);

export interface NoticeProps {
  tone?: NoticeTone;
  icon: LucideIcon;
  /** Spins the icon: only for work that is actually in flight. */
  spin?: boolean;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Extra detail under the description: failing steps, a raw error. */
  children?: React.ReactNode;
  /** The one next step: a button or a link. */
  actions?: React.ReactNode;
}

export function Notice({
  tone = "info",
  icon: Icon,
  spin = false,
  title,
  description,
  children,
  actions,
}: NoticeProps) {
  const grouped = React.useContext(GroupedContext);
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      aria-live="polite"
      className={cn(
        "flex min-w-0 flex-wrap items-start gap-3 px-4 py-3",
        !grouped && "bg-card rounded-md border"
      )}
    >
      <Icon
        aria-hidden
        className={cn("mt-0.5 size-4 shrink-0", TONE_ICON[tone], spin && "animate-spin")}
      />
      <div className="min-w-0 flex-1 basis-60">
        <p className="text-sm font-medium [overflow-wrap:anywhere]">{title}</p>
        {description && (
          <div className="text-muted-foreground mt-0.5 text-xs [overflow-wrap:anywhere]">
            {description}
          </div>
        )}
        {children && <div className="mt-2 min-w-0 text-xs">{children}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** One frame for every notice; hidden when none of them renders. */
export function OverviewNotices({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  const t = useTranslations("apps.overview");
  return (
    <GroupedContext.Provider value={true}>
      <section
        aria-label={t("noticesLabel")}
        className={cn("bg-card min-w-0 divide-y rounded-md border empty:hidden", className)}
      >
        {children}
      </section>
    </GroupedContext.Provider>
  );
}
