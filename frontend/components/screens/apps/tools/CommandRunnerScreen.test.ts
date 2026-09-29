import { describe, expect, it } from "vitest";

import { outputToLines } from "./CommandRunnerScreen";

describe("outputToLines", () => {
  it("joins a line split across chunks and splits a chunk holding many", () => {
    const lines = outputToLines([
      { channel: "stdout", text: "orders\n [X] 0001_init", ts: 1 },
      { channel: "stdout", text: "ial\n [ ] 0002\n", ts: 2 },
    ]);
    expect(lines.map((l) => l.message)).toEqual(["orders", " [X] 0001_initial", " [ ] 0002"]);
    expect(lines[1].ts).toBe(1);
  });

  it("marks stderr as errors and drops the blank padding around system lines", () => {
    const lines = outputToLines([
      { channel: "stderr", text: "ls: missing\n", ts: 1 },
      { channel: "system", text: "\n[exit 2]\n", ts: 2 },
    ]);
    expect(lines).toEqual([
      { ts: 1, message: "ls: missing", level: "error" },
      { ts: 2, message: "[exit 2]", level: "info" },
    ]);
  });

  it("breaks the line when the channel changes mid-line", () => {
    const lines = outputToLines([
      { channel: "stdout", text: "partial", ts: 1 },
      { channel: "stderr", text: "oops\n", ts: 2 },
    ]);
    expect(lines.map((l) => [l.message, l.level])).toEqual([
      ["partial", undefined],
      ["oops", "error"],
    ]);
  });
});
