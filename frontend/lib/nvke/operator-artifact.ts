import { validNvkeGuid } from "./recovery";

export interface NvkeOperatorArtifact {
  name: string;
  size: string;
  sha256: string;
}
export const DEFAULT_OPERATOR_DOWNLOAD_BYTES = 64 * 1024 * 1024;
export const MAX_OPERATOR_DOWNLOAD_BYTES = 256 * 1024 * 1024;
const decimal = /^(0|[1-9][0-9]{0,18})$/;
const sha256 = /^[0-9a-f]{64}$/;
export class NvkeOperatorArtifactError extends Error {
  constructor(readonly code: "DOWNLOAD_FAILED" | "SOURCE_CHANGED" | "TOO_LARGE") {
    super(code);
  }
}
export const validOperatorArtifact = (value: unknown): value is NvkeOperatorArtifact => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return (
    typeof row.name === "string" &&
    row.name.length > 0 &&
    row.name.length <= 4096 &&
    !/\p{C}/u.test(row.name) &&
    typeof row.size === "string" &&
    decimal.test(row.size) &&
    typeof row.sha256 === "string" &&
    sha256.test(row.sha256)
  );
};

export async function downloadNvkeOperatorArtifact(options: {
  operationId: string;
  artifact: NvkeOperatorArtifact;
  maximumBytes?: number;
  current: () => boolean;
  readChunk: (offset: string, limit: number) => Promise<unknown>;
  onProgress?: (completedBytes: number, totalBytes: number) => void;
}): Promise<Blob> {
  const { operationId, current } = options,
    artifact = { ...options.artifact };
  const maximumBytes = options.maximumBytes ?? DEFAULT_OPERATOR_DOWNLOAD_BYTES;
  const fail = () => {
    throw new NvkeOperatorArtifactError("DOWNLOAD_FAILED");
  };
  const checkpoint = () => {
    if (!current()) throw new NvkeOperatorArtifactError("SOURCE_CHANGED");
  };
  if (
    !validNvkeGuid(operationId) ||
    !validOperatorArtifact(artifact) ||
    !Number.isSafeInteger(maximumBytes) ||
    maximumBytes < 1 ||
    maximumBytes > MAX_OPERATOR_DOWNLOAD_BYTES
  )
    fail();
  if (BigInt(artifact.size) > BigInt(maximumBytes))
    throw new NvkeOperatorArtifactError("TOO_LARGE");
  const size = Number(artifact.size),
    chunks: Uint8Array[] = [];
  let complete: Uint8Array<ArrayBuffer> | null = null;
  try {
    let offset = 0;
    do {
      checkpoint();
      const limit = Math.min(262144, Math.max(1, size - offset));
      const text = await options.readChunk(String(offset), limit);
      checkpoint();
      if (typeof text !== "string" || text.length > 1048576) fail();
      const row = JSON.parse(text as string);
      if (
        !row ||
        typeof row !== "object" ||
        Array.isArray(row) ||
        Object.keys(row).length !== 7 ||
        Object.keys(row).some(
          (key) =>
            !["id", "name", "offset", "nextOffset", "size", "sha256", "dataBase64"].includes(key)
        ) ||
        row.id !== operationId ||
        row.name !== artifact.name ||
        row.offset !== String(offset) ||
        row.size !== artifact.size ||
        row.sha256 !== artifact.sha256 ||
        typeof row.nextOffset !== "string" ||
        !decimal.test(row.nextOffset) ||
        typeof row.dataBase64 !== "string" ||
        row.dataBase64.length > 349528 ||
        row.dataBase64.length % 4 !== 0 ||
        !/^[A-Za-z0-9+/]*={0,2}$/.test(row.dataBase64)
      )
        fail();
      const decoded = atob(row.dataBase64);
      if (
        btoa(decoded) !== row.dataBase64 ||
        decoded.length > limit ||
        BigInt(row.nextOffset) !== BigInt(offset) + BigInt(decoded.length) ||
        BigInt(row.nextOffset) > BigInt(size) ||
        (offset < size && decoded.length === 0)
      )
        fail();
      chunks.push(Uint8Array.from(decoded, (letter) => letter.charCodeAt(0)));
      offset += decoded.length;
      options.onProgress?.(offset, size);
      checkpoint();
    } while (offset < size);
    complete = new Uint8Array(size);
    let position = 0;
    for (const chunk of chunks) {
      complete.set(chunk, position);
      position += chunk.length;
      chunk.fill(0);
    }
    const digest = await crypto.subtle.digest("SHA-256", complete);
    checkpoint();
    const actual = Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, "0")
    ).join("");
    if (actual !== artifact.sha256) fail();
    return new Blob([complete], { type: "application/octet-stream" });
  } catch (error) {
    if (error instanceof NvkeOperatorArtifactError) throw error;
    throw new NvkeOperatorArtifactError("DOWNLOAD_FAILED");
  } finally {
    for (const chunk of chunks) chunk.fill(0);
    complete?.fill(0);
  }
}

export const operatorArtifactFilename = (operationId: string, name: string) => {
  const safe =
    name
      .split("/")
      .at(-1)
      ?.replace(/[^A-Za-z0-9_.-]/g, "_")
      .replace(/^\.+/, "")
      .slice(0, 100) || "artifact";
  return `nvke-${operationId}-${safe}`;
};
