"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

/**
 * #427 — collapsible inline output for a run row. Renders the
 * last-200-lines server-side ``output`` field inside a monospace
 * ``<pre>`` (user-selectable so an operator can copy a stack trace
 * straight out of the table) plus a "open in console" deep link to
 * the per-app logs surface for the full tail.
 */

export interface RunOutputPanelProps {
  output: string;
  consoleHref: string;
  /** Optional caption shown above the output block (e.g. job name). */
  caption?: string;
}

export function RunOutputPanel({ output, consoleHref, caption }: RunOutputPanelProps) {
  const t = useTranslations("jobs.output");
  const hasOutput = output && output.trim().length > 0;
  const lineCount = hasOutput ? output.split("\n").length : 0;
  // 200 lines is the server-side cap; the footer hint shows when we
  // hit it so the operator knows to click through for the full tail.
  const truncated = lineCount >= 200;

  return (
    <div className="bg-muted/30 flex flex-col gap-2 rounded-md border p-3">
      {caption ? <div className="text-muted-foreground text-xs font-medium">{caption}</div> : null}
      {hasOutput ? (
        <pre
          className="bg-background max-h-80 overflow-auto rounded border p-3 text-xs leading-relaxed select-text"
          style={{ userSelect: "text" }}
        >
          {output}
        </pre>
      ) : (
        <div className="text-muted-foreground text-xs italic">{t("emptyOutput")}</div>
      )}
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground text-xs">
          {truncated ? t("truncated") : t("lines", { count: lineCount })}
        </span>
        <Button asChild size="sm" variant="outline">
          <Link href={consoleHref}>{t("openInConsole")}</Link>
        </Button>
      </div>
    </div>
  );
}
