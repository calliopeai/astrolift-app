"use client";

import { useTranslations } from "next-intl";

import { QueryError } from "@/components/QueryError";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";

export interface NitroTaskReadiness {
  clusterId: string;
  version: number;
  sourceToken: string;
  checkedAt: string;
  namespace: string;
  source: unknown;
  modes: Array<{
    mode: string;
    enabled: boolean;
    state: string;
    checks: Array<{ code: string; state: string; message: string; evidence: unknown }>;
  }>;
}

export interface NitroTasksProps {
  readiness: NitroTaskReadiness | null;
  loading: boolean;
  saving: boolean;
  readOnly?: boolean;
  error?: string | null;
  onRefresh: () => void;
  onChange: (modes: string[]) => Promise<boolean>;
}

export function NitroTasksView({
  readiness,
  loading,
  saving,
  readOnly = false,
  error,
  onRefresh,
  onChange,
}: NitroTasksProps) {
  const t = useTranslations("clusterSettings.nitro");
  const status = (value: string) =>
    value === "ready" ? t("ready") : value === "not_ready" ? t("notReady") : t("unknown");
  return (
    <Section title={t("title")} description={t("description")} divided>
      <div className="flex min-w-0 flex-col gap-5">
        <Button
          type="button"
          variant="outline"
          className="w-fit"
          disabled={loading || saving}
          onClick={onRefresh}
        >
          {loading ? t("checking") : t("refresh")}
        </Button>
        {error && (
          <QueryError
            title={t("failed")}
            error={error}
            onRetry={onRefresh}
            retryLabel={t("retry")}
          />
        )}
        {loading && !readiness && <Skeleton className="h-24 w-full" />}
        {readiness && (
          <>
            <p className="text-muted-foreground text-sm">
              {t("namespace")}:{" "}
              <span className="font-mono [overflow-wrap:anywhere]">{readiness.namespace}</span>
            </p>
            {readiness.modes.length === 0 && (
              <p className="text-muted-foreground text-sm">{t("unsupported")}</p>
            )}
            {readiness.modes.map((mode) => {
              const name = mode.mode === "confidential" ? t("confidential") : t("isolated");
              const enabled = readiness.modes.filter((row) => row.enabled).map((row) => row.mode);
              return (
                <div key={mode.mode} className="flex min-w-0 flex-col gap-3 rounded-md border p-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{name}</h3>
                      <Badge variant="outline">{status(mode.state)}</Badge>
                    </div>
                    <Button
                      type="button"
                      variant={mode.enabled ? "outline" : "default"}
                      disabled={
                        readOnly || saving || loading || (!mode.enabled && mode.state !== "ready")
                      }
                      onClick={() =>
                        void onChange(
                          mode.enabled
                            ? enabled.filter((value) => value !== mode.mode)
                            : [...enabled, mode.mode]
                        )
                      }
                    >
                      {saving
                        ? t("saving")
                        : mode.enabled
                          ? t("disable", { mode: name })
                          : t("enable", { mode: name })}
                    </Button>
                  </div>
                  <p className="text-muted-foreground text-sm">
                    {mode.enabled ? t("enabled") : t("disabled")}
                  </p>
                  <dl className="flex min-w-0 flex-col gap-2 text-sm">
                    {mode.checks.map((check) => (
                      <div
                        key={check.code}
                        className="flex min-w-0 flex-col gap-1 sm:flex-row sm:justify-between sm:gap-4"
                      >
                        <dt className="[overflow-wrap:anywhere]">{check.message}</dt>
                        <dd className="text-muted-foreground shrink-0">{status(check.state)}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              );
            })}
            <details className="min-w-0 text-sm">
              <summary className="cursor-pointer">{t("source")}</summary>
              <pre className="mt-2 text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
                {JSON.stringify(
                  {
                    checkedAt: readiness.checkedAt,
                    source: readiness.source,
                    checks: readiness.modes.map((row) => ({ mode: row.mode, checks: row.checks })),
                  },
                  null,
                  2
                )}
              </pre>
            </details>
          </>
        )}
        <p className="text-muted-foreground text-sm">{t("workerCheck")}</p>
        <p className="text-muted-foreground text-sm">{t("noProof")}</p>
      </div>
    </Section>
  );
}
