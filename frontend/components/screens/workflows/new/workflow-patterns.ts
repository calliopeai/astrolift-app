/** Patterns the workflow builder offers, and the minimal manifest it imports. */

export type PatternKind =
  | "single"
  | "chained"
  | "fan_out"
  | "supervisor_worker"
  | "review_loop"
  | "advisor";

export interface PatternOption {
  value: PatternKind;
  label: string;
  description: string;
  diagram: string;
}

export const PATTERNS: PatternOption[] = [
  {
    value: "single",
    label: "Single",
    description: "One agent stage. Simple task dispatch.",
    diagram: "[ Dispatch ]",
  },
  {
    value: "chained",
    label: "Chained",
    description: "Sequential stages: the output of each feeds the next.",
    diagram: "[ A ] → [ B ] → [ C ]",
  },
  {
    value: "fan_out",
    label: "Fan-out",
    description: "Parallel stages that run simultaneously, then aggregate.",
    diagram: "[ Dispatch ] → ⌈ B ⌉ → [ Aggregate ]\n              ⌊ C ⌋",
  },
  {
    value: "supervisor_worker",
    label: "Supervisor / Worker",
    description: "Supervisor agent plans and routes work to specialised workers.",
    diagram: "[ Plan ] → [ Route ] → ⌈ Worker ⌉ → [ Aggregate ]",
  },
  {
    value: "review_loop",
    label: "Review loop",
    description: "Agent produces output, human reviews, agent revises, up to N rounds.",
    diagram: "[ Draft ] ⟷ [ Human gate ] → [ Deliver ]",
  },
  {
    value: "advisor",
    label: "Advisor",
    description: "Agent advises but humans retain final decision authority.",
    diagram: "[ Analyse ] → [ Recommend ] → [ Human gate ]",
  },
];

const PATTERN_VALUES = new Set<string>(PATTERNS.map((p) => p.value));

/** The `?pattern=` query value when it names a known pattern, else "single". */
export function parsePattern(fromQuery: string | null): PatternKind {
  return fromQuery && PATTERN_VALUES.has(fromQuery) ? (fromQuery as PatternKind) : "single";
}

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

// JSON string escaping is valid TOML basic-string escaping.
function tomlString(s: string): string {
  return JSON.stringify(s);
}

export function buildMinimalToml(input: {
  slug: string;
  name: string;
  pattern: PatternKind;
  description: string;
}): string {
  return [
    "[workflow]",
    `slug = ${tomlString(input.slug)}`,
    `name = ${tomlString(input.name)}`,
    `pattern = ${tomlString(input.pattern)}`,
    `description = ${tomlString(input.description)}`,
    "",
  ].join("\n");
}

// ─── The New workflow page's steps ───────────────────────────────────────────

/** Where a new workflow's definition comes from: a pattern to fill in the Builder, or a manifest. */
export type NewWorkflowSource = "pattern" | "manifest";

export interface NewWorkflowErrors {
  name?: string;
  slug?: string;
  toml?: string;
  /** The submit failed for a reason no field owns. */
  form?: string;
}

/** Step 1's errors, beside their fields (spec 44 §5.4); empty when it may continue. */
export function validateSource(
  source: NewWorkflowSource,
  values: { name: string; slug: string; toml: string }
): NewWorkflowErrors {
  if (source === "manifest") {
    return values.toml.trim() ? {} : { toml: "Paste or upload a workflow manifest." };
  }
  const errors: NewWorkflowErrors = {};
  if (!values.name.trim()) errors.name = "Give the workflow a name.";
  if (!(values.slug.trim() || slugify(values.name))) errors.slug = "A slug is required.";
  return errors;
}

/** A manifest's stages in the shape the workflow views draw (components/workflows/definition-line). */
export function manifestLineStages(
  stages: {
    order: number;
    kind: string;
    role: string;
    agent: string | null;
    workflow: string | null;
    onFailure: string;
    fanOut: string;
  }[]
) {
  return stages.map((s) => {
    const n = Number.parseInt(s.fanOut, 10);
    const count = Number.isFinite(n) ? n : null;
    return {
      guid: `manifest-${s.order}`,
      order: s.order,
      kind: s.kind,
      role: s.role,
      workflowRef: s.workflow ?? "",
      fanOutCount: count,
      // A fan-out that is not a number is sized from data at run time.
      fanOutDynamic: count === null && s.fanOut.trim() !== "" && s.fanOut.trim() !== "0",
      onFailure: s.onFailure,
      agentName: s.agent,
    };
  });
}
