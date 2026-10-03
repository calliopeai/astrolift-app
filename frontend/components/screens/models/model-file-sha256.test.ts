import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { ModelFileSha256 } from "./model-file-sha256";
describe("bounded incremental model-file SHA256", () => {
  it.each([0, 1, 3, 55, 56, 63, 64, 65, 127, 128, 1024, 1024 * 1024 + 73])(
    "matches native SHA256 for %s bytes across chunk boundaries",
    (size) => {
      const bytes = Uint8Array.from({ length: size }, (_, i) => (i * 37 + 17) % 256),
        h = new ModelFileSha256();
      for (let offset = 0; offset < size; offset += 113)
        h.update(bytes.subarray(offset, offset + 113));
      expect(h.digestHex()).toBe(createHash("sha256").update(bytes).digest("hex"));
      expect(() => h.update(new Uint8Array())).toThrow();
    }
  );
  it("hashes a multi-chunk file with constant buffers, without collecting chunks", () => {
    const chunk = new Uint8Array(1024 * 1024).fill(0xa5),
      native = createHash("sha256"),
      h = new ModelFileSha256();
    for (let i = 0; i < 16; i++) {
      h.update(chunk);
      native.update(chunk);
    }
    expect(h.digestHex()).toBe(native.digest("hex"));
  });
});
