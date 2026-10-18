// Writes test-results/live/report.json: each test's outcome and findings,
// with cookie values, tokens and secret-bearing query values redacted.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import type { Reporter, TestCase, TestResult } from "@playwright/test/reporter";

export const REPORT = "test-results/live/report.json";

function cookieValues(): string[] {
  const path = process.env.LIVE_STORAGE_STATE;
  if (!path || !existsSync(path)) return [];
  const state = JSON.parse(readFileSync(path, "utf8"));
  return (state.cookies ?? [])
    .map((cookie: { value: string }) => cookie.value)
    .filter((value: string) => value.length >= 8);
}

export function redact(text: string, secrets: string[]): string {
  let out = text;
  for (const secret of secrets) out = out.split(secret).join("[redacted]");
  return out
    .replace(/eyJ[\w-]+\.[\w-]+\.[\w-]*/g, "[redacted-jwt]")
    .replace(
      /([?&](?:[^=&\s"]*(?:token|code|state|session|jwt|key|secret|sig|auth)[^=&\s"]*)=)[^&\s"]*/gi,
      "$1[redacted]"
    );
}

export default class LiveReport implements Reporter {
  private tests: unknown[] = [];

  onTestEnd(test: TestCase, result: TestResult) {
    const findings = result.annotations
      .filter((annotation) => annotation.type === "live" && annotation.description)
      .map((annotation) => JSON.parse(annotation.description!));
    this.tests.push({
      title: test.title,
      project: test.parent.project()?.name,
      status: result.status,
      durationMs: result.duration,
      errors: result.errors.map((error) => error.message ?? String(error.value)),
      ...findings[0],
    });
  }

  onEnd() {
    mkdirSync("test-results/live", { recursive: true });
    const body = JSON.stringify(
      {
        baseURL: process.env.LIVE_BASE_URL,
        finishedAt: new Date().toISOString(),
        tests: this.tests,
      },
      null,
      2
    );
    writeFileSync(REPORT, redact(body, cookieValues()));
  }
}
