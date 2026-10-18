import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  AXES,
  type Cell,
  type CellMatrix,
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
} from "./substrate-cells";
import { CELLS } from "./brain-cells.fixtures";

/**
 * The matrix the forms offer is the server's (`astroliftBrainCells`, #2444);
 * the stories' copy of its answer is the backend's parity fixture, the real
 * `knowledge_store.cell_status()` output for all 192 cells.
 */
const fixture = JSON.parse(
  readFileSync(
    path.resolve(
      __dirname,
      "../../../../backend/astrolift_registry/tests/fixtures/mimwell_cells.json"
    ),
    "utf8"
  )
) as { source: string; cells: CellMatrix };

const FILES: Cell = { authority: "files", blobs: "fs", vectors: "local", graph: "edges-table" };

describe("Mimwell's cell matrix", () => {
  it("is what the server answers, in the stories as in the backend's fixture", () => {
    expect(CELLS).toEqual(fixture.cells);
    expect(CELLS).toHaveLength(
      AXES.authority.length * AXES.blobs.length * AXES.vectors.length * AXES.graph.length
    );
  });

  it("gives each cell the server's verdict and reason", () => {
    expect(cellStatus(FILES, CELLS)).toEqual({ supported: true });
    expect(cellStatus({ ...FILES, authority: "d1" }, CELLS)).toEqual({
      supported: false,
      reason: "d1 is a projection target, never an authority",
    });
  });

  it("refuses nothing until the matrix arrives", () => {
    expect(cellStatus({ ...FILES, authority: "d1" }, null)).toBeNull();
    expect(optionStatus(FILES, "authority", "d1", null)).toBeNull();
    expect(
      substrateProblems({ ...DEFAULT_SUBSTRATE, cell: { ...FILES, authority: "d1" } }, null)
    ).toEqual([]);
  });

  it("disables an option that would make the cell unsupported", () => {
    expect(optionStatus(FILES, "graph", "age", CELLS)).toEqual({
      supported: false,
      reason: "age is a Postgres extension",
    });
    expect(optionStatus({ ...FILES, authority: "postgres" }, "graph", "age", CELLS)).toEqual({
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
    expect(substrateProblems(DEFAULT_SUBSTRATE, CELLS)).toEqual([]);
    expect(
      substrateProblems(
        {
          cell: { authority: "files", blobs: "db-blob", vectors: "local", graph: "edges-table" },
          persistence: [],
          replicas: 21,
          image: "ghcr.io/acme/a b",
          port: 70000,
        },
        CELLS
      )
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

  it("starts a graduation from the cell in place, on the port the layer serves on", () => {
    expect(
      draftFromSubstrate({
        authority: "sqlite",
        blobs: "fs",
        vectors: "local",
        graph: "edges-table",
        persistence: ["sqlite-volume", "kv"],
        replicas: 3,
        port: 9000,
      })
    ).toEqual({
      cell: { authority: "sqlite", blobs: "fs", vectors: "local", graph: "edges-table" },
      persistence: ["sqlite-volume"],
      replicas: 3,
      image: "",
      port: 9000,
    });
    expect(draftFromSubstrate(null)).toEqual(DEFAULT_SUBSTRATE);
  });
});
