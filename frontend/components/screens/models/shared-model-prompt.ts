import type { GetSharedModelPromptReadinessQuery } from "@/graphql/__generated__/operations";
export type SharedPromptReadiness = NonNullable<
  GetSharedModelPromptReadinessQuery["astroliftSharedModelPromptReadiness"]
>;
export function validSharedPromptLimits(
  value: SharedPromptReadiness | null | undefined
): value is SharedPromptReadiness {
  return Boolean(
    value &&
    [
      value.maxPromptChars,
      value.maxOutputTokens,
      value.promptsPerMinute,
      value.maxWaitSeconds,
    ].every(Number.isInteger) &&
    value.maxPromptChars > 0 &&
    value.maxPromptChars <= 4000 &&
    value.maxOutputTokens > 0 &&
    value.maxOutputTokens <= 128 &&
    value.promptsPerMinute > 0 &&
    value.promptsPerMinute <= 6 &&
    value.maxWaitSeconds >= 20 &&
    value.maxWaitSeconds <= 60
  );
}
export type SharedPromptResult =
  | { ok: true; reply: string; latencyMs: number | null; totalTokens: number | null }
  | {
      ok: false;
      message: string;
      code: string | null;
      currentVersion?: number | null;
      requestedVersion?: number | null;
    };
