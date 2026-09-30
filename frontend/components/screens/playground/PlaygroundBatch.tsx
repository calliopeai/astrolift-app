"use client";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { PlaygroundBatchProps } from "./playground.types";
export type { PlaygroundBatchProps } from "./playground.types";

/** One actual prompt per line; cancellation stops future calls, not an admitted server job. */
export function PlaygroundBatch(p: PlaygroundBatchProps) {
  const t = useTranslations("playground");
  return (
    <section className="flex min-w-0 flex-col gap-3" aria-label={t("batch")}>
      <p className="text-muted-foreground text-sm">{t("batchLimits")}</p>
      <label htmlFor="playground-batch-input">{t("batchInput")}</label>
      <Textarea
        id="playground-batch-input"
        value={p.input}
        onChange={(e) => p.setInput(e.target.value)}
        rows={8}
        disabled={p.running}
        maxLength={24006}
      />
      {p.invalid && <p role="alert">{t("batchInvalid")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button
          onClick={p.onRun}
          disabled={!p.canRun || p.running || !p.inputs.length || p.invalid}
        >
          {t(p.running ? "running" : "runAll")}
        </Button>
        <Button variant="outline" onClick={p.onCancel} disabled={!p.running}>
          {t("cancel")}
        </Button>
        <Button variant="outline" onClick={p.onExportCsv} disabled={!p.results.length || p.running}>
          CSV
        </Button>
        <Button
          variant="outline"
          onClick={p.onExportJsonl}
          disabled={!p.results.length || p.running}
        >
          JSONL
        </Button>
        <Button variant="outline" onClick={p.onCopyJson} disabled={!p.results.length || p.running}>
          {t("copyResults")}
        </Button>
      </div>
      {p.cancelled && <p role="status">{t("cancelled")}</p>}
      <p>
        {t("batchCount", { ok: p.results.filter((r) => r.ok).length, total: p.results.length })}
      </p>
      {!p.results.length ? (
        <p className="text-muted-foreground text-sm">{t("emptyResults")}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {p.results.map((r, i) => (
            <li key={i} className="rounded border p-3 text-sm">
              <p className="break-words whitespace-pre-wrap">{r.input}</p>
              <p className="mt-2 break-words whitespace-pre-wrap" role={r.ok ? undefined : "alert"}>
                {r.ok ? r.output : t(`errors.${r.error ?? "failed"}`)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
