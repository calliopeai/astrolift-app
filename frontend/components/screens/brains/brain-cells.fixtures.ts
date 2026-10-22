import cells from "./brain-cells.fixture.json";
import type { CellMatrix } from "./substrate-cells";

/**
 * `astroliftBrainCells` as the server answers it (#2444): Mimwell's matrix
 * at the pinned commit, all 192 cells, the same rows as the backend's
 * `mimwell_cells.json` (substrate-cells.test.ts holds them equal).
 */
export const CELLS: CellMatrix = cells;
