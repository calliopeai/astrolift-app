import { describe, expect, it } from "vitest";

import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";

import {
  countLevels,
  filterLogLines,
  formatLogFile,
  logFilename,
  toLogLines,
} from "./app-log-lines";

const line = (message: string, podName = "web-1", container = "web"): AstroliftAppLogLine => ({
  message,
  podName,
  container,
  stream: "stdout",
  timestamp: "2026-09-28T12:00:04.000Z",
});

describe("toLogLines", () => {
  it("keeps one mapped object per source line, so LogView's chunks stay memoised", () => {
    const src = [line("GET / 200"), line("ERROR boom")];
    const a = toLogLines(src);
    const b = toLogLines([...src, line("next")]);
    expect(b[0]).toBe(a[0]);
    expect(b[1]).toBe(a[1]);
    expect(a[1].level).toBe("error");
  });

  it("prefixes pod and container for many-pod views", () => {
    const [mapped] = toLogLines([line("hello", "web-2", "envoy")], { withPodName: true });
    expect(mapped.message).toBe("web-2/envoy hello");
  });
});

describe("filterLogLines", () => {
  const lines = toLogLines([line("INFO ready"), line("WARN slow"), line("ERROR Timeout")]);

  it("returns the same array when nothing narrows it", () => {
    expect(filterLogLines(lines, "all", "  ")).toBe(lines);
  });

  it("narrows by level and by text, case-insensitive", () => {
    expect(filterLogLines(lines, "warn", "").map((l) => l.message)).toEqual(["WARN slow"]);
    expect(filterLogLines(lines, "all", "timeout").map((l) => l.message)).toEqual([
      "ERROR Timeout",
    ]);
    expect(filterLogLines(lines, "info", "timeout")).toEqual([]);
  });

  it("counts every level", () => {
    expect(countLevels(lines)).toEqual({ error: 1, warn: 1, info: 1, debug: 0, other: 0 });
  });
});

describe("the download file", () => {
  it("names the file from safe parts and a timestamp", () => {
    const name = logFilename(
      [
        { value: "store/front", fallback: "app" },
        { value: null, fallback: "pod" },
      ],
      new Date("2026-09-28T12:00:00.000Z")
    );
    expect(name).toBe("store-front-pod-2026-09-28T12-00-00-000Z.log");
  });

  it("writes one ISO-stamped line per entry", () => {
    expect(formatLogFile(toLogLines([line("a"), line("b")]))).toBe(
      "2026-09-28T12:00:04.000Z a\n2026-09-28T12:00:04.000Z b\n"
    );
  });
});
