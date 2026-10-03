export type LocalFileManifest = { name: string; sizeBytes: string; sha256: string };
export function hashLocalModelFiles(
  files: File[],
  signal: AbortSignal,
  onProgress: (name: string, percent: number) => void,
  createWorker = () =>
    new Worker(new URL("./model-file-hash.worker.ts", import.meta.url), { type: "module" })
) {
  return new Promise<LocalFileManifest[]>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Stopped", "AbortError"));
      return;
    }
    if (typeof Worker === "undefined") {
      reject(new Error("HASH_UNSUPPORTED"));
      return;
    }
    let worker: Worker;
    try {
      worker = createWorker();
    } catch {
      reject(new Error("HASH_UNSUPPORTED"));
      return;
    }
    let completed = false;
    const finish = (error: Error | null, result?: LocalFileManifest[]) => {
      if (completed) return;
      completed = true;
      signal.removeEventListener("abort", stop);
      worker.terminate();
      if (error) reject(error);
      else resolve(result!);
    };
    const stop = () => finish(new DOMException("Stopped", "AbortError"));
    signal.addEventListener("abort", stop, { once: true });
    worker.onerror = (event) => {
      event.preventDefault();
      finish(new Error("HASH_FAILED"));
    };
    worker.onmessage = (event: MessageEvent) => {
      if (completed || signal.aborted) return;
      const data = event.data;
      if (data.type === "progress") {
        if (
          Number.isInteger(data.index) &&
          files[data.index] &&
          data.total === files[data.index].size &&
          data.bytes >= 0 &&
          data.bytes <= data.total
        )
          onProgress(files[data.index].name, Math.floor((data.bytes / data.total) * 100));
      } else if (data.type === "complete") {
        if (
          !Array.isArray(data.files) ||
          data.files.length !== files.length ||
          data.files.some(
            (file: LocalFileManifest, index: number) =>
              file.name !== files[index].name ||
              file.sizeBytes !== String(files[index].size) ||
              !/^[a-f0-9]{64}$/.test(file.sha256)
          )
        ) {
          finish(new Error("HASH_FAILED"));
          return;
        }
        finish(null, data.files);
      } else finish(new Error("HASH_FAILED"));
    };
    try {
      worker.postMessage({ files });
    } catch {
      finish(new Error("HASH_FAILED"));
    }
  });
}
export function validateLocalFiles(files: File[]): "files" | "sizes" | null {
  const metadata = new Set([
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "tokenizer.model",
    "vocab.json",
    "merges.txt",
    "chat_template.jinja",
  ]);
  const names = new Set(files.map((file) => file.name));
  if (
    !files.length ||
    files.length > 256 ||
    names.size !== files.length ||
    !names.has("config.json") ||
    (!names.has("tokenizer.json") && !names.has("tokenizer.model")) ||
    !files.some((file) => file.name.endsWith(".safetensors")) ||
    files.some(
      (file) =>
        !/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(file.name) ||
        file.name.includes("..") ||
        (!metadata.has(file.name) &&
          !file.name.endsWith(".safetensors") &&
          !file.name.endsWith(".safetensors.index.json"))
    )
  )
    return "files";
  if (
    files.some(
      (file) =>
        file.size <= 0 ||
        file.size > 5_000_000_000 ||
        (file.name.endsWith(".json") && file.size > 64 * 1024 ** 2)
    ) ||
    files.reduce((sum, file) => sum + file.size, 0) > 200 * 1024 ** 3
  )
    return "sizes";
  return null;
}
