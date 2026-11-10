import { Buffer } from "node:buffer";
import { expect, it, vi } from "vitest";
import {
  downloadNvkeOperatorArtifact,
  operatorArtifactFilename,
  DEFAULT_OPERATOR_DOWNLOAD_BYTES,
} from "./operator-artifact";
const operationId = "123e4567-e89b-12d3-a456-426614174001";
const hex = async (bytes: Uint8Array<ArrayBuffer>) =>
  Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
async function setup(size = 262149) {
  const bytes = new Uint8Array(size);
  for (let i = 0; i < size; i++) bytes[i] = i % 251;
  const artifact = { name: "private/stdout", size: String(size), sha256: await hex(bytes) };
  const readChunk = vi.fn(async (offset: string, limit: number): Promise<unknown> => {
    const chunk = bytes.slice(Number(offset), Number(offset) + limit);
    let binary = "";
    for (const byte of chunk) binary += String.fromCharCode(byte);
    return JSON.stringify({
      id: operationId,
      name: artifact.name,
      size: artifact.size,
      sha256: artifact.sha256,
      offset,
      nextOffset: String(Number(offset) + chunk.length),
      dataBase64: btoa(binary),
    });
  });
  return {
    bytes,
    options: { operationId, artifact, current: () => true, readChunk, onProgress: vi.fn() },
  };
}
const contents = (blob: Blob) =>
  new Promise<Uint8Array>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = reject;
    reader.readAsArrayBuffer(blob);
  });
it("downloads binary chunks only after continuity, identity and the complete SHA-256 agree", async () => {
  const { bytes, options } = await setup();
  const blob = await downloadNvkeOperatorArtifact(options);
  expect(Buffer.compare(await contents(blob), bytes)).toBe(0);
  expect(options.readChunk.mock.calls).toEqual([
    ["0", 262144],
    ["262144", 5],
  ]);
  expect(options.onProgress.mock.calls).toEqual([
    [262144, 262149],
    [262149, 262149],
  ]);
});
it("verifies an empty artifact with exactly one original-job chunk request", async () => {
  const { options } = await setup(0);
  expect((await downloadNvkeOperatorArtifact(options)).size).toBe(0);
  expect(options.readChunk).toHaveBeenCalledExactlyOnceWith("0", 1);
});
it.each([
  { id: "123e4567-e89b-12d3-a456-426614174002" },
  { name: "different" },
  { offset: "1" },
  { nextOffset: "9999999999999999999" },
  { nextOffset: "01" },
  { size: "9007199254740993" },
  { sha256: "a".repeat(64) },
  { dataBase64: "AA==\n" },
  { dataBase64: "AB==" },
  { dataBase64: "" },
  { dataBase64: "x".repeat(349529) },
  { privateField: "PRIVATE_DIAGNOSTIC" },
])("refuses malformed or changed chunk metadata without offering a partial file", async (patch) => {
  const { options } = await setup(5),
    original = options.readChunk.getMockImplementation()!;
  options.readChunk.mockImplementation(async (offset, limit) =>
    JSON.stringify({ ...JSON.parse((await original(offset, limit)) as string), ...patch })
  );
  await expect(downloadNvkeOperatorArtifact(options)).rejects.toThrow("DOWNLOAD_FAILED");
});
it("refuses a valid-looking set of ranges with a wrong final file hash", async () => {
  const { options } = await setup(),
    original = options.readChunk.getMockImplementation()!;
  options.readChunk.mockImplementation(async (offset, limit) => {
    const row = JSON.parse((await original(offset, limit)) as string);
    if (offset !== "0") row.dataBase64 = btoa("wrong");
    return JSON.stringify(row);
  });
  await expect(downloadNvkeOperatorArtifact(options)).rejects.toThrow("DOWNLOAD_FAILED");
  expect(options.readChunk).toHaveBeenCalledTimes(2);
});
it("refuses an oversized artifact before making a request, preserving exact native decimal sizes", async () => {
  const { options } = await setup(1);
  options.artifact.size = "9007199254740993";
  await expect(downloadNvkeOperatorArtifact(options)).rejects.toThrow("TOO_LARGE");
  expect(options.readChunk).not.toHaveBeenCalled();
});
it.each([0, 1.5, NaN, Infinity, 256 * 1024 * 1024 + 1])(
  "refuses an invalid memory budget",
  async (maximumBytes) => {
    const { options } = await setup(1);
    await expect(downloadNvkeOperatorArtifact({ ...options, maximumBytes })).rejects.toThrow(
      "DOWNLOAD_FAILED"
    );
    expect(options.readChunk).not.toHaveBeenCalled();
  }
);
it("honors an explicit reviewed budget", async () => {
  const { options } = await setup(5);
  await expect(downloadNvkeOperatorArtifact({ ...options, maximumBytes: 4 })).rejects.toThrow(
    "TOO_LARGE"
  );
  expect(options.readChunk).not.toHaveBeenCalled();
  expect(DEFAULT_OPERATOR_DOWNLOAD_BYTES).toBe(64 * 1024 * 1024);
});
it("drops private bytes when the caller changes after a chunk read", async () => {
  const { options } = await setup(),
    original = options.readChunk.getMockImplementation()!;
  let current = true;
  options.current = () => current;
  options.readChunk.mockImplementation(async (offset, limit) => {
    const result = await original(offset, limit);
    current = false;
    return result;
  });
  await expect(downloadNvkeOperatorArtifact(options)).rejects.toThrow("SOURCE_CHANGED");
  expect(options.readChunk).toHaveBeenCalledTimes(1);
});
it("supports cancellation between ranges without another read", async () => {
  const { options } = await setup();
  let current = true;
  options.current = () => current;
  options.onProgress.mockImplementation(() => {
    current = false;
  });
  await expect(downloadNvkeOperatorArtifact(options)).rejects.toThrow("SOURCE_CHANGED");
  expect(options.readChunk).toHaveBeenCalledTimes(1);
});
it("freezes descriptor metadata across asynchronous reads", async () => {
  const { options } = await setup(5),
    original = options.readChunk.getMockImplementation()!;
  options.readChunk.mockImplementation(async (offset, limit) => {
    const text = await original(offset, limit);
    options.artifact.name = "changed";
    return text;
  });
  expect((await downloadNvkeOperatorArtifact(options)).size).toBe(5);
});
it("replaces raw transport diagnostics with a fixed error", async () => {
  const { options } = await setup(1);
  options.readChunk.mockRejectedValue(new Error("PRIVATE_TRANSPORT_DETAILS"));
  await expect(downloadNvkeOperatorArtifact(options)).rejects.toThrow(/^DOWNLOAD_FAILED$/);
});
it("uses a bounded download filename without native path or leading-dot traversal", () => {
  expect(operatorArtifactFilename(operationId, "../../private/config.yml")).toBe(
    `nvke-${operationId}-config.yml`
  );
  expect(operatorArtifactFilename(operationId, ".")).toBe(`nvke-${operationId}-artifact`);
  expect(operatorArtifactFilename(operationId, "x".repeat(4096)).length).toBeLessThan(150);
});
