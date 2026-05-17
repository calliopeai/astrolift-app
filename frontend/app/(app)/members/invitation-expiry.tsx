"use client";

/**
 * Live countdown for an invitation row (#418).
 *
 * Renders ``expires in 6d 4h`` / ``5h 12m`` / ``32m`` / ``expired``
 * with a red tint when the remaining window is under 24h. The
 * component owns a 60s ticker so multiple rows on the page share a
 * single useEffect interval pattern but ride their own re-render —
 * acceptable up to a few hundred rows; if the invitation list ever
 * grows past that the ticker can be lifted into a parent context.
 *
 * Server-side rendering deliberately renders the static label first
 * and switches to the dynamic one after hydration; this avoids
 * server/client mismatches when the worker clock and the operator
 * clock disagree by a few minutes.
 */

import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface Props {
  expiresAt: string;
  className?: string;
}

const REFRESH_MS = 60_000;
const ONE_MIN = 60_000;
const ONE_HOUR = 60 * ONE_MIN;
const ONE_DAY = 24 * ONE_HOUR;

export function InvitationExpiryBadge({ expiresAt, className }: Props) {
  const t = useTranslations("lists.invitationExpiry");
  const [now, setNow] = React.useState<number>(() => Date.now());
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
    const tick = () => setNow(Date.now());
    tick();
    const id = window.setInterval(tick, REFRESH_MS);
    return () => window.clearInterval(id);
  }, []);

  const target = React.useMemo(() => new Date(expiresAt).getTime(), [expiresAt]);
  const remainingMs = target - now;
  const tooltip = React.useMemo(() => new Date(expiresAt).toLocaleString(), [expiresAt]);

  const label = formatRemaining(remainingMs, t);
  const isExpired = remainingMs <= 0;
  const isUrgent = !isExpired && remainingMs < ONE_DAY;

  // Pre-hydration fallback: render the absolute timestamp so the
  // server-rendered HTML is stable and only the live label drops in
  // after mount. Avoids a hydration warning if the server clock and
  // client clock disagree on the "expires in Xd" rounding.
  if (!mounted) {
    return (
      <span title={tooltip} className={cn("text-muted-foreground text-sm", className)}>
        {tooltip}
      </span>
    );
  }

  return (
    <span title={tooltip} className={cn("inline-flex items-center gap-1.5", className)}>
      {isExpired ? (
        <Badge variant="secondary" className="capitalize">
          {t("expired")}
        </Badge>
      ) : (
        <Badge
          variant={isUrgent ? "destructive" : "outline"}
          className={cn("font-mono text-xs", isUrgent ? "" : "text-muted-foreground border-dashed")}
        >
          {label}
        </Badge>
      )}
    </span>
  );
}

/**
 * Format a remaining millisecond window into a 2-piece label
 * (``6d 4h``, ``5h 12m``, ``32m``). Returns the localized "expired"
 * string for any non-positive value. Kept exported for tests; the
 * formatter has no side effects beyond reading translations.
 */
type ExpiryTranslator = (k: string, v?: Record<string, string | number | Date>) => string;

export function formatRemaining(remainingMs: number, t: ExpiryTranslator): string {
  if (remainingMs <= 0) return t("expired");
  const days = Math.floor(remainingMs / ONE_DAY);
  const hours = Math.floor((remainingMs % ONE_DAY) / ONE_HOUR);
  const minutes = Math.floor((remainingMs % ONE_HOUR) / ONE_MIN);
  if (days >= 1) {
    return t("days", { days, hours });
  }
  if (hours >= 1) {
    return t("hours", { hours, minutes });
  }
  return t("minutes", { minutes: Math.max(minutes, 1) });
}
