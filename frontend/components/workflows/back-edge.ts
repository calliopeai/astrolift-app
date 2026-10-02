/** The native authoring shape. Invalid or unknown source data is not guessed. */
export interface WorkflowBackEdge {
  to: string;
  when: "gate_rejected" | "stage_failed" | "output_equals" | "always";
  max_rounds: number;
  on_exhausted: "fail" | "escalate" | "continue";
  source_format?: "flowise_loop_1_2";
  source_target?: string;
  source_label?: string;
  fallback_message?: string | null;
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
    !["gate_rejected", "stage_failed", "output_equals", "always"].includes(String(v.when)) ||
    !["fail", "escalate", "continue"].includes(String(v.on_exhausted ?? "fail"))
  )
    return null;
  const sourceFields = ["source_format", "source_target", "source_label", "fallback_message"];
  const imported = sourceFields.some((key) => key in v);
  if (
    imported &&
    (v.source_format !== "flowise_loop_1_2" ||
      v.when !== "always" ||
      typeof v.source_target !== "string" ||
      !v.source_target ||
      v.source_target.length > 100 ||
      typeof v.source_label !== "string" ||
      v.source_label.length > 1000 ||
      ("fallback_message" in v &&
        v.fallback_message !== null &&
        (typeof v.fallback_message !== "string" ||
          v.fallback_message.length > 4096 ||
          v.fallback_message.includes("{{"))))
  )
    return null;
  if (imported) {
    const source = (v.source_target as string)
      .normalize("NFKD")
      .replace(/[^\x00-\x7F]/g, "")
      .toLowerCase();
    const slug = source
      .replace(/[^\w\s-]/g, "")
      .replace(/[-\s]+/g, "-")
      .replace(/^[-_]+|[-_]+$/g, "");
    if (v.to !== `flow_${slug}`.slice(0, 100)) return null;
  }
  if (v.on_exhausted === "continue" && !imported) return null;
  if (
    Object.keys(v).some(
      (key) =>
        !["to", "when", "max_rounds", "on_exhausted", "path", "value", ...sourceFields].includes(
          key
        )
    )
  )
    return null;
  if (v.when !== "output_equals" && ("path" in v || "value" in v)) return null;
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
