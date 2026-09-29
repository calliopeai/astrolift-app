import { describe, expect, it } from "vitest";

import { buildCsv } from "./exportCsv";

type Row = { name: string; note?: string | null; count: number };
const COLUMNS = [
  { header: "Name", value: (r: Row) => r.name },
  { header: "Note", value: (r: Row) => r.note },
  { header: "Count", value: (r: Row) => r.count },
];

describe("buildCsv", () => {
  it("writes a header and CRLF rows", () => {
    expect(buildCsv([{ name: "ada", count: 2 }], COLUMNS)).toBe("Name,Note,Count\r\nada,,2\r\n");
  });

  it("quotes commas, quotes, newlines and edge spaces", () => {
    const csv = buildCsv(
      [
        { name: 'say "hi", ok', note: "two\nlines", count: 1 },
        { name: " pad", count: 0 },
      ],
      COLUMNS
    );
    expect(csv).toBe('Name,Note,Count\r\n"say ""hi"", ok","two\nlines",1\r\n" pad",,0\r\n');
  });

  it("neutralises cells a spreadsheet would run as formulas", () => {
    const csv = buildCsv([{ name: "=HYPERLINK(1)", note: "@x", count: -3 }], COLUMNS);
    expect(csv).toBe("Name,Note,Count\r\n'=HYPERLINK(1),'@x,-3\r\n");
  });

  it("writes only the header for no rows", () => {
    expect(buildCsv([], COLUMNS)).toBe("Name,Note,Count\r\n");
  });
});
