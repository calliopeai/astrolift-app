"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { batchToCsv, batchToJsonl, downloadBlob, type BatchResult } from "./saved-sessions";
import type { PromptExecutor } from "./playground.types";

/** Sequential explicit submissions; no synthetic outputs, fan-out, or retries. */
export function usePlaygroundBatch(
  invoke: PromptExecutor,
  contextKey: string,
  canSend: boolean,
  maxPromptChars: number
) {
  const t = useTranslations("playground");
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [results, setResults] = useState<BatchResult[]>([]);
  const [epoch, setEpoch] = useState({ key: contextKey, version: 0 });
  if (epoch.key !== contextKey) setEpoch({ key: contextKey, version: epoch.version + 1 });
  const generation = useRef({
    key: contextKey,
    version: 0,
    mounted: true,
    cancel: false,
    busy: false,
  });
  useLayoutEffect(() => {
    generation.current = {
      key: contextKey,
      version: epoch.version,
      mounted: true,
      cancel: false,
      busy: false,
    };
  }, [contextKey, epoch.version]);
  const [resultKey, setResultKey] = useState(contextKey);
  const executor = useRef(invoke);
  useLayoutEffect(() => {
    executor.current = invoke;
  }, [invoke]);
  const inputs = input
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const invalid =
    inputs.length > 6 || inputs.some((line) => line.length > Math.min(4000, maxPromptChars));
  useEffect(() => {
    generation.current.mounted = true;
    return () => {
      generation.current.mounted = false;
      generation.current.version += 1;
      generation.current.cancel = true;
    };
  }, []);
  useEffect(() => {
    setInput("");
    setResults([]);
    setResultKey(contextKey);
    setRunning(false);
    setCancelled(false);
  }, [contextKey]);
  const onRun = async () => {
    const current = generation.current;
    if (
      current.key !== contextKey ||
      current.version !== epoch.version ||
      !current.mounted ||
      current.busy ||
      !contextKey ||
      !canSend ||
      !inputs.length ||
      invalid
    )
      return;
    current.busy = true;
    current.cancel = false;
    setRunning(true);
    setCancelled(false);
    setResults([]);
    setResultKey(contextKey);
    const collected: BatchResult[] = [];
    const version = current.version;
    try {
      for (const line of inputs) {
        if (current.cancel || !current.mounted || generation.current.version !== version) break;
        const response = await executor.current(line);
        if (!current.mounted || generation.current.version !== version) break;
        collected.push({
          input: line,
          output: response.ok ? response.reply : "",
          ok: response.ok,
          error: response.error,
          latencyMs: response.latencyMs,
          totalTokens: response.totalTokens,
        });
        setResults([...collected]);
        if (!response.ok) break;
      }
    } finally {
      current.busy = false;
      if (current.mounted && generation.current.version === version) setRunning(false);
    }
  };
  const visible = resultKey === contextKey ? results : [];
  return {
    input,
    setInput,
    inputs,
    running: resultKey === contextKey && running,
    cancelled: resultKey === contextKey && cancelled,
    canRun: canSend,
    invalid,
    results: visible,
    onRun,
    onCancel: () => {
      generation.current.cancel = true;
      setCancelled(true);
    },
    onExportCsv: () => {
      if (visible.length) downloadBlob(batchToCsv(visible), "playground-batch.csv", "text/csv");
    },
    onExportJsonl: () => {
      if (visible.length)
        downloadBlob(batchToJsonl(visible), "playground-batch.jsonl", "application/x-ndjson");
    },
    onCopyJson: async () => {
      if (!visible.length) return;
      try {
        await navigator.clipboard.writeText(JSON.stringify(visible, null, 2));
        toast.success(t("copied"));
      } catch {
        toast.error(t("copyFailed"));
      }
    },
  };
}
