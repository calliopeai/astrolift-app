/** Only reconciliation metadata may survive navigation; submitted inputs stay in memory. */
export type StartKind = "workflow" | "pipeline";
export type InputField = {
  name: string;
  kind: string;
  required: boolean;
  hasDefault: boolean;
  default: unknown;
  sensitive: boolean;
  simple: boolean;
  enumValues: unknown[] | null;
  constraints: Record<string, unknown>;
};
export type InputContract = {
  schema: Record<string, unknown> | null;
  digest: string;
  supported: boolean;
  error: string;
  acceptsInputs: boolean;
  supportsSimpleForm: boolean;
  fields: InputField[];
};
export type StartReview = {
  id: string;
  name: string;
  revision: string | number;
  enabled: boolean;
  contract?: InputContract;
  defaultBranch?: string;
};
export type StartReceipt = {
  id: string;
  temporalWorkflowId: string;
  temporalRunId: string | null;
  dispatchStatus: string;
  dispatchLastError?: string | null;
};
export type StartStamp = {
  kind: StartKind;
  targetId: string;
  requestId: string;
  revision: string | number;
  inputSchemaDigest?: string;
  ref?: string;
};
export function stampKey(kind: StartKind, org: string, actor: string, target: string): string {
  return `astro-reviewed-start:${kind}:${org}:${actor}:${target}`;
}
export function readStamp(raw: string | null, kind: StartKind, target: string): StartStamp | null {
  try {
    const value: unknown = JSON.parse(raw ?? "null");
    if (!value || typeof value !== "object") return null;
    const v = value as Record<string, unknown>;
    if (
      v.kind !== kind ||
      v.targetId !== target ||
      typeof v.requestId !== "string" ||
      !v.requestId ||
      v.requestId.length > 128 ||
      !["string", "number"].includes(typeof v.revision)
    )
      return null;
    if (kind === "workflow" && typeof v.inputSchemaDigest !== "string") return null;
    return {
      kind,
      targetId: target,
      requestId: v.requestId,
      revision: v.revision as string | number,
      ...(typeof v.inputSchemaDigest === "string"
        ? { inputSchemaDigest: v.inputSchemaDigest }
        : {}),
      ...(typeof v.ref === "string" ? { ref: v.ref } : {}),
    };
  } catch {
    return null;
  }
}
export function initialFieldValues(contract: InputContract): Record<string, string | undefined> {
  return Object.fromEntries(
    contract.fields.map((f) => [
      f.name,
      f.hasDefault && !f.sensitive
        ? f.kind === "string"
          ? String(f.default ?? "")
          : JSON.stringify(f.default)
        : undefined,
    ])
  );
}
export function simpleInputs(
  contract: InputContract,
  values: Record<string, string | undefined>
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const field of contract.fields) {
    const value = values[field.name];
    if (value === undefined) continue; // the server applies canonical defaults and required constraints
    if (field.kind === "integer" || field.kind === "number") {
      if (!value.trim()) throw new Error("number");
      const number = Number(value);
      if (!Number.isFinite(number) || (field.kind === "integer" && !Number.isInteger(number)))
        throw new Error("number");
      result[field.name] = number;
    } else if (field.kind === "boolean") {
      if (value !== "true" && value !== "false") throw new Error("boolean");
      result[field.name] = value === "true";
    } else result[field.name] = value;
  }
  return result;
}
