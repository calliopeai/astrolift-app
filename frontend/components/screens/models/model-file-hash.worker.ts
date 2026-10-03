import { ModelFileSha256 } from "./model-file-sha256";
// File is structured-cloned as a blob handle. Only a 1MiB slice is allocated
// at a time, inside a worker; terminating the worker cancels further hashing.
self.onmessage = async (event: MessageEvent<{ files: File[] }>) => {
  try {
    const files = event.data.files;
    if (!Array.isArray(files) || !files.length || files.length > 256) throw new Error();
    const result: { name: string; sizeBytes: string; sha256: string }[] = [];
    for (const [index, file] of files.entries()) {
      if (file.size <= 0 || file.size > 5_000_000_000) throw new Error();
      const hash = new ModelFileSha256();
      for (let offset = 0; offset < file.size; offset += 1024 * 1024) {
        const chunk = new Uint8Array(await file.slice(offset, offset + 1024 * 1024).arrayBuffer());
        hash.update(chunk);
        self.postMessage({
          type: "progress",
          index,
          bytes: Math.min(file.size, offset + chunk.length),
          total: file.size,
        });
      }
      result.push({ name: file.name, sizeBytes: String(file.size), sha256: hash.digestHex() });
    }
    self.postMessage({ type: "complete", files: result });
  } catch {
    self.postMessage({ type: "failed" });
  }
};
