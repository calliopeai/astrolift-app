/** The native authoring shape. Invalid or unknown source data is not guessed. */
export interface WorkflowBackEdge {
  to: string;
  when: "gate_rejected" | "stage_failed" | "output_equals";
  max_rounds: number;
  on_exhausted: "fail" | "escalate";
  path?: string;
  value?: string | number | boolean | null;
}

export function readBackEdge(raw: unknown): WorkflowBackEdge | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const v = raw as Record<string, unknown>;
  if (
    typeof v.to !== "string" ||
    !v.to ||
    !Number.isInteger(v.max_rounds) ||
    (v.max_rounds as number) < 1 ||
    (v.max_rounds as number) > 20 ||
    !["gate_rejected", "stage_failed", "output_equals"].includes(String(v.when)) ||
    !["fail", "escalate"].includes(String(v.on_exhausted ?? "fail"))
  )
    return null;
  if (
    v.when === "output_equals" &&
    (typeof v.path !== "string" ||
      !/^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*){0,15}$/.test(v.path) ||
      !("value" in v) ||
      !isScalar(v.value))
  )
    return null;
  return { ...v, on_exhausted: v.on_exhausted ?? "fail" } as unknown as WorkflowBackEdge;
}

export function isScalar(value: unknown): value is WorkflowBackEdge["value"] {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  );
}

export function edgeFromDraft(raw: unknown, valueJson?: string): unknown {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return raw ?? {};
  const v = raw as Record<string, unknown>;
  if (v.when !== "output_equals" || valueJson === undefined) return raw;
  const value: unknown = JSON.parse(valueJson);
  if (!isScalar(value)) throw new Error("A JSON scalar is required");
  return { ...v, value };
}
