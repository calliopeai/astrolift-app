"use client";

import { CopyIcon, DownloadIcon, PlayIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import type { usePlaygroundBatch } from "./use-playground-batch";

export type PlaygroundBatchProps = ReturnType<typeof usePlaygroundBatch>;

/** The playground's batch tab: one input per line, run sequentially, export the results. */
export function PlaygroundBatch({
  input,
  setInput,
  inputs,
  running,
  results,
  onRun,
  onExportCsv,
  onExportJsonl,
  onCopyJson,
}: PlaygroundBatchProps) {
  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="grid flex-1 grid-cols-1 gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">Inputs (one per line)</label>
            <span className="text-muted-foreground text-xs">{inputs.length} rows</span>
          </div>
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder='{"prompt": "What is 2+2?"}'
            rows={16}
            className="resize-none font-mono text-xs"
          />
          <div className="flex gap-2">
            <Button onClick={onRun} disabled={running || inputs.length === 0}>
              <PlayIcon className="mr-1 size-3" />
              {running ? "Running…" : "Run all"}
            </Button>
            <Button variant="outline" onClick={onExportCsv} disabled={results.length === 0}>
              <DownloadIcon className="mr-1 size-3" /> CSV
            </Button>
            <Button variant="outline" onClick={onExportJsonl} disabled={results.length === 0}>
              <DownloadIcon className="mr-1 size-3" /> JSONL
            </Button>
            <Button variant="outline" onClick={onCopyJson} disabled={results.length === 0}>
              <CopyIcon className="mr-1 size-3" /> Copy
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">Results</label>
            <span className="text-muted-foreground text-xs">
              {results.filter((r) => r.ok).length}/{results.length} ok
            </span>
          </div>
          <div className="bg-muted/30 flex-1 overflow-auto rounded-md border">
            {results.length === 0 ? (
              <div className="text-muted-foreground flex h-full items-center justify-center p-6 text-center text-xs">
                Results will appear here after running.
              </div>
            ) : (
              <ul className="divide-border divide-y text-xs">
                {results.map((r, i) => (
                  <li key={i} className="p-2">
                    <div className="text-muted-foreground truncate">{r.input}</div>
                    <div className="mt-1 whitespace-pre-wrap">{r.ok ? r.output : r.error}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
