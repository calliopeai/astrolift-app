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
    description: "Sequential stages — output of each feeds the next.",
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
    description: "Agent produces output, human reviews, agent revises — up to N rounds.",
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
