"use client";

import { useTranslations } from "next-intl";

import { ExternalLinkIcon, GitCompareIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import type { useDeploymentComparison } from "./use-deployment-comparison";

export type CompareDeploymentsSheetViewProps = ReturnType<typeof useDeploymentComparison> & {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  deployA: AstroliftDeployment;
  deployB: AstroliftDeployment;
};

/** Side sheet diffing two selected deployments: image summary and manifest changes (#652). */
export function CompareDeploymentsSheetView({
  open,
  onOpenChange,
  deployA,
  deployB,
  comparison: cmp,
  loading,
  error,
}: CompareDeploymentsSheetViewProps) {
  const t = useTranslations("apps.deployments.compare");
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col gap-4 sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2 font-mono text-base">
            <GitCompareIcon className="size-4" />
            {deployA.imageTag || deployA.id.slice(0, 7)} →{" "}
            {deployB.imageTag || deployB.id.slice(0, 7)}
            {cmp?.compareUrl && (
              <a
                href={cmp.compareUrl}
                target="_blank"
                rel="noreferrer"
                className="text-muted-foreground hover:text-foreground ml-auto inline-flex items-center gap-1 text-xs"
              >
                {t("source")}
                <ExternalLinkIcon className="size-3" />
              </a>
            )}
          </SheetTitle>
          <SheetDescription>
            {cmp ? (
              <span className="font-mono text-xs">
                {cmp.baseSha.slice(0, 7)}...{cmp.headSha.slice(0, 7)}
              </span>
            ) : loading ? (
              t("loading")
            ) : error ? (
              <span className="text-destructive">{error}</span>
            ) : (
              "—"
            )}
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-5 overflow-y-auto px-4 pb-4">
          {loading && !cmp ? (
            <div className="space-y-2">
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : cmp ? (
            <>
              <section className="space-y-2">
                <h4 className="text-sm font-semibold">{t("image")}</h4>
                {cmp.imageDiffSummary ? (
                  <pre className="bg-muted/40 border-border overflow-x-auto rounded-md border p-3 font-mono text-xs whitespace-pre-wrap">
                    {cmp.imageDiffSummary}
                  </pre>
                ) : (
                  <p className="text-muted-foreground text-xs">{t("noImage")}</p>
                )}
              </section>

              <section className="space-y-2">
                <h4 className="text-sm font-semibold">
                  {t("manifest")}{" "}
                  <span className="text-muted-foreground text-xs font-normal">
                    {t("changes", { count: cmp.manifestDiff.length })}
                  </span>
                </h4>
                {cmp.manifestDiff.length === 0 ? (
                  <p className="text-muted-foreground text-xs">{t("identical")}</p>
                ) : (
                  <ul className="space-y-1.5">
                    {cmp.manifestDiff.map((entry, i) => (
                      <ManifestDiffRow key={`${entry.path}-${i}`} entry={entry} />
                    ))}
                  </ul>
                )}
              </section>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function ManifestDiffRow({
  entry,
}: {
  entry: { op: string; path: string; before: unknown; after: unknown };
}) {
  const t = useTranslations("apps.deployments.compare");
  const badge =
    entry.op === "add" ? (
      <Badge className="border-success-border bg-success/15 text-success-fg">{t("add")}</Badge>
    ) : entry.op === "remove" ? (
      <Badge variant="destructive">{t("remove")}</Badge>
    ) : (
      <Badge className="border-warning-border bg-warning/15 text-warning-fg">{t("replace")}</Badge>
    );

  return (
    <li className="border-border bg-muted/20 space-y-1 rounded-md border p-2 font-mono text-xs">
      <div className="flex flex-wrap items-center gap-2">
        {badge}
        <code className="break-all">{entry.path}</code>
      </div>
      {entry.op === "add" && (
        <pre className="text-success-fg break-all whitespace-pre-wrap">
          {jsonValue(entry.after)}
        </pre>
      )}
      {entry.op === "remove" && (
        <pre className="text-destructive break-all whitespace-pre-wrap">
          {jsonValue(entry.before)}
        </pre>
      )}
      {entry.op === "replace" && (
        <div className="space-y-1">
          <pre className="text-destructive break-all whitespace-pre-wrap">
            − {jsonValue(entry.before)}
          </pre>
          <pre className="text-success-fg break-all whitespace-pre-wrap">
            + {jsonValue(entry.after)}
          </pre>
        </div>
      )}
    </li>
  );
}

function jsonValue(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}
