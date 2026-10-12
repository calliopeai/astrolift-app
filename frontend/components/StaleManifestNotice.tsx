import { AlertTriangleIcon, FileWarningIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

/**
 * Surfaces a deploy that rendered the *stored* manifest rather than the
 * repo's current one (#1553).
 *
 * Every deploy re-fetches `astrolift.toml` first (#1535), but that refresh is
 * best-effort: it refuses when an unpushed staged draft would be clobbered
 * (`diverged`), and it gives up when the file can't be read (`fetch_failed`).
 * Both cases used to exist only as a worker log line, so "I pushed a fix and
 * redeployed and nothing changed" looked like the platform caching the
 * manifest forever. Each status gets the specific remedy, because the two
 * need opposite actions from the operator.
 */
export function StaleManifestNotice({
  status,
  error,
  appSlug,
  context = "deploy",
  className,
}: {
  status: string | null | undefined;
  error: string | null | undefined;
  appSlug: string;
  /**
   * Where the outcome came from. `deploy` is a rollout that rendered the
   * stored manifest; `registration` is an app that registered without its
   * workloads. Same underlying statuses, different consequence to explain.
   */
  context?: "deploy" | "registration";
  className?: string;
}) {
  const t = useTranslations("staleManifest");
  // applied / in_sync / no_source are healthy or not-applicable; empty means
  // the row predates the field.
  const failed = status === "diverged" || status === "fetch_failed" || status === "parse_failed";
  if (!failed) return null;

  const diverged = status === "diverged";
  const registration = context === "registration";
  const Icon = diverged ? FileWarningIcon : AlertTriangleIcon;

  return (
    <div
      className={cn(
        "border-warning-border bg-warning-bg text-warning-fg flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm",
        className
      )}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 space-y-1">
        <p className="font-semibold">
          {registration
            ? t("registrationTitle")
            : diverged
              ? t("divergedTitle")
              : t("fallbackTitle")}
        </p>
        <p className="text-foreground/80">
          {t.rich(registration ? "registrationBody" : diverged ? "divergedBody" : "fallbackBody", {
            code: (chunks) => <code className="font-mono text-xs">{chunks}</code>,
          })}
        </p>
        {error && <p className="text-muted-foreground text-2xs font-mono break-words">{error}</p>}
        <Link
          href={`/apps/${appSlug}/manifest`}
          className="text-primary inline-block font-medium underline-offset-4 hover:underline"
        >
          {diverged ? t("reviewDraft") : t("openManifest")} →
        </Link>
      </div>
    </div>
  );
}
