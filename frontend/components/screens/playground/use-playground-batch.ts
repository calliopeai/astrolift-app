"use client";

import { useMemo, useState } from "react";
import { toast } from "sonner";

import { type BatchResult, batchToCsv, batchToJsonl, downloadBlob } from "./saved-sessions";

/** Batch tab state and actions; runs only while the batch tab is shown. */
export function usePlaygroundBatch(model: string) {
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<BatchResult[]>([]);

  const inputs = useMemo(
    () =>
      input
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0),
    [input]
  );

  const handleRun = async () => {
    if (inputs.length === 0) {
      toast.error("Provide at least one input line.");
      return;
    }
    setRunning(true);
    const collected: BatchResult[] = [];
    // Sequential execution so a batch run hits one model at a time —
    // simpler error model and easier on rate limits than fan-out.
    for (const line of inputs) {
      const res = await simulateBatchCall(line, model);
      collected.push(res);
      setResults([...collected]);
    }
    setRunning(false);
    toast.success(`Batch complete: ${collected.length} results`);
  };

  const handleExportCsv = () => {
    if (results.length === 0) {
      toast.error("Run the batch before exporting.");
      return;
    }
    downloadBlob(batchToCsv(results), "playground-batch.csv", "text/csv");
  };

  const handleExportJsonl = () => {
    if (results.length === 0) {
      toast.error("Run the batch before exporting.");
      return;
    }
    downloadBlob(batchToJsonl(results), "playground-batch.jsonl", "application/x-ndjson");
  };

  const handleCopyJson = async () => {
    if (results.length === 0) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(results, null, 2));
      toast.success("Results copied");
    } catch {
      toast.error("Could not copy");
    }
  };

  return {
    input,
    setInput,
    inputs,
    running,
    results,
    onRun: handleRun,
    onExportCsv: handleExportCsv,
    onExportJsonl: handleExportJsonl,
    onCopyJson: handleCopyJson,
  };
}

async function simulateBatchCall(input: string, model: string): Promise<BatchResult> {
  // Placeholder while playground has no real backend — keeps the UI
  // surface end-to-end so an operator can validate the batch flow
  // independent of the inference wiring. Real inference call lands
  // when the playground gateway exists.
  await new Promise<void>((resolve) => window.setTimeout(resolve, 100));
  return {
    input,
    output: `[${model}] simulated response for input length ${input.length}`,
    ok: true,
  };
}
