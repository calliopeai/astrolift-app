/**
 * CSV export for Admin lists (spec 44 §5.1: audit, members, spend), from the
 * list's `⋯` menu. `buildCsv` is pure and unit-tested; `exportCsv` downloads
 * through an object URL.
 */

export interface CsvColumn<TRow> {
  header: string;
  value: (row: TRow) => string | number | boolean | null | undefined;
}

// A cell a spreadsheet would run as a formula (=, +, -, @, tab, CR) is
// prefixed with a quote, so an exported name like `=HYPERLINK(…)` is text.
const FORMULA_LEAD = /^[=+\-@\t\r]/;

function cell(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  if (typeof value === "string" && FORMULA_LEAD.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) || text !== text.trim() ? `"${text.replace(/"/g, '""')}"` : text;
}

/** RFC 4180: CRLF line ends, quoted where a cell needs it, header first. */
export function buildCsv<TRow>(rows: TRow[], columns: CsvColumn<TRow>[]): string {
  const lines = [
    columns.map((c) => cell(c.header)).join(","),
    ...rows.map((row) => columns.map((c) => cell(c.value(row))).join(",")),
  ];
  return `${lines.join("\r\n")}\r\n`;
}

/** Download `rows` as `filename` (`.csv` is added when missing). */
export function exportCsv<TRow>(filename: string, rows: TRow[], columns: CsvColumn<TRow>[]) {
  // The BOM makes Excel read the file as UTF-8 rather than the locale's codepage.
  const blob = new Blob(["﻿", buildCsv(rows, columns)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
