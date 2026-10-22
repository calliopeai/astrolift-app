/**
 * A semantic layer's substrate in Mimwell's own terms (#2424, the backing
 * model of #2427): a cell of four placements plus what the host attaches.
 *
 * `cellStatus` reads Mimwell's matrix as `astroliftBrainCells` returns it
 * (#2444), so the create and graduation forms only offer supported cells.
 * Until the matrix arrives nothing is refused here; the server checks the
 * same matrix on every write and its refusal is shown on the field it names.
 * Pure.
 */
import type {
  BrainAuthority,
  BrainBlobs,
  BrainGraph,
  BrainPersistence,
  BrainSubstrateInput,
  BrainVectors,
} from "@/graphql/__generated__/operations";

export const AXES = {
  authority: ["files", "sqlite", "postgres", "mysql", "mongo", "d1"],
  blobs: ["fs", "db-blob", "s3", "azure-blob"],
  vectors: ["local", "in-db", "edge-db", "external"],
  graph: ["edges-table", "age"],
} as const;

export type Axis = keyof typeof AXES;
export const AXIS_ORDER: readonly Axis[] = ["authority", "blobs", "vectors", "graph"];

export type Cell = { [A in Axis]: (typeof AXES)[A][number] };

export const DEFAULT_CELL: Cell = {
  authority: "files",
  blobs: "fs",
  vectors: "local",
  graph: "edges-table",
};

/** What an Astrolift host can attach; d1 and kv are the Worker path's. */
export const PERSISTENCE = ["sqlite-volume", "efs", "postgres", "s3"] as const;
export type Persistence = (typeof PERSISTENCE)[number];

/** Persistence that holds data: graduation never drops it (a cutover is the brain's). */
export const DATA_PERSISTENCE: readonly Persistence[] = ["sqlite-volume", "efs", "postgres"];

/** Persistence a brain holds data on, which graduation keeps attached. */
export function heldPersistence(current: readonly string[]): Persistence[] {
  return DATA_PERSISTENCE.filter((p) => current.includes(p));
}

export const MAX_REPLICAS = 20;
export const DEFAULT_PORT = 8080;

/** Mimwell's matrix, one row per cell, as `astroliftBrainCells` returns it. */
export type CellMatrix = readonly {
  authority: string;
  blobs: string;
  vectors: string;
  graph: string;
  status: string;
  reason: string;
}[];

/** Supported, or not with Mimwell's reason as the server words it. */
export type CellVerdict = { supported: true } | { supported: false; reason: string };

/** `cell`'s verdict in `matrix`; null while the matrix has not arrived. */
export function cellStatus(cell: Cell, matrix: CellMatrix | null): CellVerdict | null {
  if (!matrix) return null;
  const row = matrix.find((c) => AXIS_ORDER.every((axis) => c[axis] === cell[axis]));
  if (row?.status === "supported") return { supported: true };
  return { supported: false, reason: row?.reason ?? "" };
}

/** The verdict for `cell` with one placement changed: what a picker option would make. */
export function optionStatus<A extends Axis>(
  cell: Cell,
  axis: A,
  value: (typeof AXES)[A][number],
  matrix: CellMatrix | null
): CellVerdict | null {
  return cellStatus({ ...cell, [axis]: value }, matrix);
}

/** The persistence a cell cannot be hosted without, and the placement that asks for it. */
export function requiredPersistence(cell: Cell): Map<Persistence, Axis> {
  const out = new Map<Persistence, Axis>();
  if (cell.authority === "sqlite") out.set("sqlite-volume", "authority");
  if (cell.authority === "postgres") out.set("postgres", "authority");
  if (cell.graph === "age" && !out.has("postgres")) out.set("postgres", "graph");
  if (cell.blobs === "s3") out.set("s3", "blobs");
  return out;
}

/** What is attached: the chosen extras plus what the cell needs, in a fixed order. */
export function effectivePersistence(
  cell: Cell,
  chosen: readonly Persistence[],
  locked: readonly Persistence[] = []
): Persistence[] {
  const required = requiredPersistence(cell);
  return PERSISTENCE.filter((p) => required.has(p) || chosen.includes(p) || locked.includes(p));
}

/** A value from the API as a cell placement, or null when this build does not know it. */
export function placementOf<A extends Axis>(axis: A, value: string): Cell[A] | null {
  return (AXES[axis] as readonly string[]).includes(value) ? (value as Cell[A]) : null;
}

/** The cell an API substrate names, or null when any placement is unknown here. */
export function cellOf(substrate: {
  authority: string;
  blobs: string;
  vectors: string;
  graph: string;
}): Cell | null {
  const authority = placementOf("authority", substrate.authority);
  const blobs = placementOf("blobs", substrate.blobs);
  const vectors = placementOf("vectors", substrate.vectors);
  const graph = placementOf("graph", substrate.graph);
  return authority && blobs && vectors && graph ? { authority, blobs, vectors, graph } : null;
}

export function persistenceOf(values: readonly string[]): Persistence[] {
  return PERSISTENCE.filter((p) => values.includes(p));
}

/** Mimwell's name for a placement as the GraphQL enum spells it: `db-blob` is `DB_BLOB`. */
function enumName(value: string): string {
  return value.toUpperCase().replace(/-/g, "_");
}

export interface SubstrateDraft {
  cell: Cell;
  /** Persistence the person added beyond what the cell needs. */
  persistence: Persistence[];
  replicas: number;
  /** Blank: the platform builds the edition's portal Dockerfile. */
  image: string;
  port: number;
}

export const DEFAULT_SUBSTRATE: SubstrateDraft = {
  cell: DEFAULT_CELL,
  persistence: [],
  replicas: 1,
  image: "",
  port: DEFAULT_PORT,
};

export type SubstrateProblem = "cell" | "replicas" | "image" | "port";

/** What keeps a draft from being sent, as the field that says so. */
export function substrateProblems(
  draft: SubstrateDraft,
  matrix: CellMatrix | null
): SubstrateProblem[] {
  const out: SubstrateProblem[] = [];
  if (cellStatus(draft.cell, matrix)?.supported === false) out.push("cell");
  if (!Number.isInteger(draft.replicas) || draft.replicas < 1 || draft.replicas > MAX_REPLICAS)
    out.push("replicas");
  if (/\s/.test(draft.image.trim())) out.push("image");
  if (!Number.isInteger(draft.port) || draft.port < 1 || draft.port > 65535) out.push("port");
  return out;
}

/**
 * The input both writes take. `locked` is persistence that must stay (what
 * a brain already holds data on). A blank image is null: on create the
 * platform builds the edition's Dockerfile, on graduation the server keeps
 * the current image.
 */
export function substrateInput(
  draft: SubstrateDraft,
  { locked = [] }: { locked?: readonly Persistence[] } = {}
): BrainSubstrateInput {
  const image = draft.image.trim();
  return {
    authority: enumName(draft.cell.authority) as BrainAuthority,
    blobs: enumName(draft.cell.blobs) as BrainBlobs,
    vectors: enumName(draft.cell.vectors) as BrainVectors,
    graph: enumName(draft.cell.graph) as BrainGraph,
    persistence: effectivePersistence(draft.cell, draft.persistence, locked).map(
      (p) => enumName(p) as BrainPersistence
    ),
    replicas: draft.replicas,
    image: image || null,
    port: draft.port,
  };
}

/** The form a graduation starts from: the cell, persistence and port the layer has now. */
export function draftFromSubstrate(
  substrate: {
    authority: string;
    blobs: string;
    vectors: string;
    graph: string;
    persistence: readonly string[];
    replicas: number;
    port: number;
  } | null
): SubstrateDraft {
  return {
    cell: (substrate && cellOf(substrate)) || DEFAULT_CELL,
    persistence: persistenceOf(substrate?.persistence ?? []),
    replicas: substrate?.replicas ?? 1,
    image: "",
    port: substrate?.port ?? DEFAULT_PORT,
  };
}

/** The fields of a substrate a server refusal can name, so the form marks the right one. */
export const SUBSTRATE_FIELDS: readonly string[] = [
  "substrate",
  "authority",
  "blobs",
  "vectors",
  "graph",
  "persistence",
  "replicas",
  "image",
  "port",
];
