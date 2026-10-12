"use client";

import { QueryError } from "@/components/QueryError";

import { type ReactNode, useMemo, useState } from "react";
import { Loader2Icon, XIcon } from "lucide-react";
import { useTimeZone, useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Sparkline } from "@/components/viz";
import type { FormSubmission } from "@/graphql/forms/forms.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { buildSubmissionsSparkline, type SparklinePoint } from "./submissions-sparkline";

const PAGE_SIZE = 25;

/**
 * Submissions tab for a form (#437 scope B).
 *
 * Paginates client-side over the loaded submissions list — the
 * backend already returns a slug-scoped slice but capped at a server
 * limit; the cursor/page wiring lives on a from:backend follow-up so
 * the UI here uses client-side paging to be useful immediately.
 *
 * Each row → detail sheet that auto-expands long text fields and
 * surfaces file references (any value looking like a URL) as
 * clickable links.
 */
export function FormSubmissionsTab({
  submissions,
  loading,
  error,
  onRetry,
}: {
  submissions: FormSubmission[];
  loading: boolean;
  error?: { message: string } | null;
  onRetry?: () => void;
}) {
  const t = useTranslations("formSubmissions");
  const fmt = useFormatters();
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<FormSubmission | null>(null);

  const sparkline = useSparkline(submissions);

  const totalPages = Math.max(1, Math.ceil(submissions.length / PAGE_SIZE));
  const pageItems = useMemo(
    () => submissions.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [submissions, page]
  );

  if (loading && submissions.length === 0) {
    return (
      <div className="flex items-center justify-center p-12">
        <Loader2Icon className="text-muted-foreground size-5 animate-spin" />
      </div>
    );
  }

  if (error && submissions.length === 0)
    return <QueryError title={t("loadFailed")} error={error} onRetry={onRetry} />;
  if (submissions.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed p-8 text-center text-sm">
        {t("empty")}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <SubmissionsSparkline data={sparkline} total={submissions.length} />

      <div className="overflow-hidden rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-muted-foreground text-xs uppercase">
            <tr>
              <th className="px-3 py-2 text-left">{t("submitted")}</th>
              <th className="px-3 py-2 text-left">{t("version")}</th>
              <th className="px-3 py-2 text-left">{t("status")}</th>
              <th className="px-3 py-2 text-left">{t("preview")}</th>
            </tr>
          </thead>
          <tbody>
            {pageItems.map((sub, idx) => (
              <tr
                key={`${sub.submittedAt}-${idx}`}
                onClick={() => setSelected(sub)}
                className="hover:bg-accent/50 cursor-pointer border-t"
              >
                <td className="px-3 py-2 font-mono text-xs">
                  {sub.submittedAt ? fmt.formatDateTime(sub.submittedAt) : ""}
                </td>
                <td className="px-3 py-2 text-xs">v{sub.formVersion}</td>
                <td className="px-3 py-2">
                  <Badge variant="outline">{sub.status}</Badge>
                </td>
                <td className="text-muted-foreground truncate px-3 py-2 text-xs">
                  {summarizePayload(sub.payload, t("emptySummary"))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="text-muted-foreground flex items-center justify-between text-xs">
          <span>
            {t("pageOf", { page: page + 1, pages: totalPages, total: submissions.length })}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              {t("previous")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            >
              {t("next")}
            </Button>
          </div>
        </div>
      )}

      <SubmissionDetailSheet submission={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function SubmissionDetailSheet({
  submission,
  onClose,
}: {
  submission: FormSubmission | null;
  onClose: () => void;
}) {
  const t = useTranslations("formSubmissions");
  const fmt = useFormatters();
  return (
    <Sheet open={submission !== null} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-xl">
        {submission && (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2">
                {t("detailTitle")}
                <Badge variant="outline">{submission.status}</Badge>
              </SheetTitle>
              <SheetDescription>
                {t("detailDescription", {
                  form: submission.formName,
                  version: submission.formVersion,
                  time: submission.submittedAt ? fmt.formatDateTime(submission.submittedAt) : "",
                })}
              </SheetDescription>
            </SheetHeader>
            <div className="overflow-y-auto px-4 pb-4">
              <PayloadView payload={submission.payload} />
            </div>
            <div className="flex justify-end border-t px-4 py-3">
              <Button variant="outline" size="sm" onClick={onClose}>
                <XIcon className="mr-1 size-3" /> {t("close")}
              </Button>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function PayloadView({ payload }: { payload: Record<string, unknown> }) {
  const t = useTranslations("formSubmissions");
  const entries = Object.entries(payload ?? {});
  if (entries.length === 0) {
    return <p className="text-muted-foreground text-sm">{t("emptyPayload")}</p>;
  }
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-[max-content_1fr]">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted-foreground text-xs uppercase">{key}</dt>
          <dd className="text-sm">{renderValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function renderValue(value: unknown): ReactNode {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground italic">—</span>;
  }
  if (typeof value === "string") {
    if (isUrlLike(value)) {
      return (
        <a href={value} target="_blank" rel="noreferrer" className="text-primary underline">
          {value}
        </a>
      );
    }
    if (value.length > 80 || value.includes("\n")) {
      return <pre className="bg-muted/40 max-h-64 overflow-auto rounded p-2 text-xs">{value}</pre>;
    }
    return <span>{value}</span>;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="font-mono text-sm">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    return (
      <pre className="bg-muted/40 max-h-64 overflow-auto rounded p-2 text-xs">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  return (
    <pre className="bg-muted/40 max-h-64 overflow-auto rounded p-2 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function isUrlLike(s: string): boolean {
  if (s.length > 2048) return false;
  return /^(https?:\/\/|s3:\/\/|gs:\/\/|\/uploads\/)/i.test(s);
}

function summarizePayload(payload: Record<string, unknown>, emptyLabel: string): string {
  const entries = Object.entries(payload ?? {}).slice(0, 3);
  if (entries.length === 0) return emptyLabel;
  return entries.map(([k, v]) => `${k}: ${formatLeaf(v)}`).join(" · ");
}

function formatLeaf(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v.length > 40 ? `${v.slice(0, 37)}…` : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.length}]`;
  return "{…}";
}

// ─── sparkline ────────────────────────────────────────────────────────

function useSparkline(submissions: FormSubmission[]): SparklinePoint[] {
  const timeZone = useTimeZone() ?? "UTC";
  return useMemo(() => buildSubmissionsSparkline(submissions, timeZone), [submissions, timeZone]);
}

function SubmissionsSparkline({ data, total }: { data: SparklinePoint[]; total: number }) {
  const t = useTranslations("formSubmissions");
  return (
    <div className="bg-muted/30 flex items-end justify-between gap-4 rounded-md border p-3">
      <div>
        <p className="text-muted-foreground text-xs uppercase">{t("last30Days")}</p>
        <p className="text-2xl font-bold">{total}</p>
        <p className="text-muted-foreground text-xs">
          {t("newInWindow", { count: data.reduce((acc, d) => acc + d.count, 0) })}
        </p>
      </div>
      <Sparkline
        data={data.map((p) => p.count)}
        width={280}
        height={48}
        variant="area"
        className="text-primary"
        ariaLabel={t("trend")}
      />
    </div>
  );
}
