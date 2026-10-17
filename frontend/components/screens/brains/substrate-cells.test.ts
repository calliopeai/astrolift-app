import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  AXES,
  type Cell,
  cellOf,
  cellStatus,
  DEFAULT_SUBSTRATE,
  draftFromSubstrate,
  effectivePersistence,
  heldPersistence,
  optionStatus,
  requiredPersistence,
  substrateInput,
  substrateProblems,
  type UnsupportedReason,
} from "./substrate-cells";

/**
 * The cell matrix the forms offer is Mimwell's (#2424): the backend's parity
 * fixture is the real `knowledge_store.cell_status()` output for all 192
 * cells at the pinned commit, so this copy is held to the same source.
 */
const fixture = JSON.parse(
  readFileSync(
    path.resolve(
      __dirname,
      "../../../../backend/astrolift_registry/tests/fixtures/mimwell_cells.json"
    ),
    "utf8"
  )
) as {
  source: string;
  cells: (Cell & { status: string; reason: string })[];
};

/** Mimwell's reason sentences, by the key the forms show them under. */
function reasonKey(reason: string): UnsupportedReason {
  if (reason.startsWith("d1 is a projection target")) return "d1Authority";
  if (reason.startsWith("db-blob keeps blobs")) return "dbBlobNeedsDatabase";
  if (reason.startsWith("in-db vectors are SQL vector columns")) return "inDbVectorsMongo";
  if (reason.startsWith("in-db vectors live in the authority database"))
    return "inDbVectorsNeedSql";
  if (reason.startsWith("age is a Postgres extension")) return "ageNeedsPostgres";
  throw new Error(`a reason this port does not know: ${reason}`);
}

describe("Mimwell's cell matrix", () => {
  it("covers every cell the fixture has, pinned to the backend's commit", () => {
    expect(fixture.source).toContain("7e103f9");
    expect(fixture.cells).toHaveLength(
      AXES.authority.length * AXES.blobs.length * AXES.vectors.length * AXES.graph.length
    );
  });

  it.each(
    fixture.cells.map((c) => [`${c.authority}/${c.blobs}/${c.vectors}/${c.graph}`, c] as const)
  )("%s matches cell_status()", (_name, row) => {
    const verdict = cellStatus(row);
    // Mimwell plans no placements at this commit; every cell is one or the other.
    expect(["supported", "unsupported"]).toContain(row.status);
    if (row.status === "supported") expect(verdict).toEqual({ supported: true });
    else expect(verdict).toEqual({ supported: false, reason: reasonKey(row.reason) });
  });

  it("disables an option that would make the cell unsupported", () => {
    const files: Cell = { authority: "files", blobs: "fs", vectors: "local", graph: "edges-table" };
    expect(optionStatus(files, "graph", "age")).toEqual({
      supported: false,
      reason: "ageNeedsPostgres",
    });
    expect(optionStatus(files, "authority", "d1")).toEqual({
      supported: false,
      reason: "d1Authority",
    });
    expect(optionStatus({ ...files, authority: "postgres" }, "graph", "age")).toEqual({
      supported: true,
    });
  });
});

describe("persistence", () => {
  it("is what the cell needs on Astrolift", () => {
    expect([
      ...requiredPersistence({
        authority: "sqlite",
        blobs: "fs",
        vectors: "local",
        graph: "edges-table",
      }),
    ]).toEqual([["sqlite-volume", "authority"]]);
    expect([
      ...requiredPersistence({
        authority: "postgres",
        blobs: "s3",
        vectors: "in-db",
        graph: "age",
      }),
    ]).toEqual([
      ["postgres", "authority"],
      ["s3", "blobs"],
    ]);
    expect([
      ...requiredPersistence({
        authority: "files",
        blobs: "fs",
        vectors: "local",
        graph: "edges-table",
      }),
    ]).toEqual([]);
  });

  it("adds the person's own picks and what must stay, in one order", () => {
    const cell: Cell = { authority: "sqlite", blobs: "s3", vectors: "local", graph: "edges-table" };
    expect(effectivePersistence(cell, ["efs"])).toEqual(["sqlite-volume", "efs", "s3"]);
    expect(effectivePersistence(DEFAULT_SUBSTRATE.cell, [], ["postgres"])).toEqual(["postgres"]);
  });

  it("keeps the persistence a layer holds data on, and never s3", () => {
    expect(heldPersistence(["s3", "efs", "postgres", "sqlite-volume"])).toEqual([
      "sqlite-volume",
      "efs",
      "postgres",
    ]);
  });
});

describe("the input both writes send", () => {
  it("spells Mimwell's names as the GraphQL enums", () => {
    expect(
      substrateInput({
        cell: { authority: "postgres", blobs: "db-blob", vectors: "in-db", graph: "age" },
        persistence: ["efs"],
        replicas: 2,
        image: "  ",
        port: 8080,
      })
    ).toEqual({
      authority: "POSTGRES",
      blobs: "DB_BLOB",
      vectors: "IN_DB",
      graph: "AGE",
      persistence: ["EFS", "POSTGRES"],
      replicas: 2,
      image: null,
      port: 8080,
    });
  });

  it("keeps locked persistence attached on graduation", () => {
    expect(substrateInput(DEFAULT_SUBSTRATE, { locked: ["sqlite-volume"] }).persistence).toEqual([
      "SQLITE_VOLUME",
    ]);
  });

  it("names what keeps a draft from being sent", () => {
    expect(substrateProblems(DEFAULT_SUBSTRATE)).toEqual([]);
    expect(
      substrateProblems({
        cell: { authority: "files", blobs: "db-blob", vectors: "local", graph: "edges-table" },
        persistence: [],
        replicas: 21,
        image: "ghcr.io/acme/a b",
        port: 70000,
      })
    ).toEqual(["cell", "replicas", "image", "port"]);
  });
});

describe("reading a substrate back", () => {
  it("takes the API's names, and gives up on a placement it does not know", () => {
    expect(cellOf({ authority: "postgres", blobs: "s3", vectors: "in-db", graph: "age" })).toEqual({
      authority: "postgres",
      blobs: "s3",
      vectors: "in-db",
      graph: "age",
    });
    expect(
      cellOf({ authority: "duckdb", blobs: "fs", vectors: "local", graph: "edges-table" })
    ).toBeNull();
  });

  it("starts a graduation from the cell in place, at Mimwell's default port", () => {
    expect(
      draftFromSubstrate({
        authority: "sqlite",
        blobs: "fs",
        vectors: "local",
        graph: "edges-table",
        persistence: ["sqlite-volume", "kv"],
        replicas: 3,
      })
    ).toEqual({
      cell: { authority: "sqlite", blobs: "fs", vectors: "local", graph: "edges-table" },
      persistence: ["sqlite-volume"],
      replicas: 3,
      image: "",
      port: 8080,
    });
    expect(draftFromSubstrate(null)).toEqual(DEFAULT_SUBSTRATE);
  });
});
