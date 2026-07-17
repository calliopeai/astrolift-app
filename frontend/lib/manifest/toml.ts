/**
 * Minimal, dependency-free TOML codec scoped to the astrolift.toml manifest
 * (#1110). The repo deliberately ships no TOML parser, and there is no
 * backend structured→TOML path for the app manifest (updateManifest is
 * text-in/text-out), so the visual config builder needs a client-side
 * round-trip. This is intentionally NOT a full TOML implementation — it
 * covers the constructs real manifests use (tables, arrays-of-tables,
 * dotted keys, scalars, arrays, inline tables) and *fails safe* on anything
 * it doesn't understand (multi-line strings, dates) by throwing, so callers
 * fall back to the raw editor instead of silently corrupting a manifest.
 *
 * Losslessness is *semantic*, not textual: comments and inline-vs-header
 * table style are not preserved, but the parsed value tree round-trips.
 * `roundTripSafe` proves this per-document before the form is allowed to edit.
 */

export type TomlValue = string | number | boolean | TomlValue[] | TomlTable;
export interface TomlTable {
  [key: string]: TomlValue;
}

export class TomlError extends Error {}

function isPlainObject(v: unknown): v is TomlTable {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

// ─── Parser ────────────────────────────────────────────────────────────────

class Scanner {
  private i = 0;
  private readonly s: string;
  constructor(s: string) {
    this.s = s;
  }

  private eof(): boolean {
    return this.i >= this.s.length;
  }

  private peek(): string {
    return this.s[this.i] ?? "";
  }

  /** Skip spaces, tabs, newlines, and `# …` comments between statements. */
  skipTrivia(): void {
    while (!this.eof()) {
      const c = this.peek();
      if (c === " " || c === "\t" || c === "\r" || c === "\n") {
        this.i++;
      } else if (c === "#") {
        while (!this.eof() && this.peek() !== "\n") this.i++;
      } else {
        break;
      }
    }
  }

  /** Skip inline spaces/tabs only (within a statement). */
  private skipInlineWs(): void {
    while (!this.eof() && (this.peek() === " " || this.peek() === "\t")) this.i++;
  }

  parse(): TomlTable {
    const root: TomlTable = {};
    let current: TomlTable = root;
    for (;;) {
      this.skipTrivia();
      if (this.eof()) break;
      if (this.peek() === "[") {
        current = this.parseHeader(root);
      } else {
        this.parseAssignment(current);
      }
    }
    return root;
  }

  private parseHeader(root: TomlTable): TomlTable {
    this.i++; // consume '['
    const isArray = this.peek() === "[";
    if (isArray) this.i++;
    this.skipInlineWs();
    const path = this.parseKeyPath();
    this.skipInlineWs();
    if (this.peek() !== "]") throw new TomlError("unterminated table header");
    this.i++;
    if (isArray) {
      if (this.peek() !== "]") throw new TomlError("unterminated array-of-tables header");
      this.i++;
    }
    return this.applyHeader(root, path, isArray);
  }

  /** Navigate/create the table that subsequent keys attach to. */
  private applyHeader(root: TomlTable, path: string[], isArray: boolean): TomlTable {
    let node: TomlTable = root;
    for (let k = 0; k < path.length - 1; k++) {
      node = this.descend(node, path[k]);
    }
    const leaf = path[path.length - 1];
    if (isArray) {
      let arr = node[leaf];
      if (arr === undefined) {
        arr = [];
        node[leaf] = arr;
      }
      if (!Array.isArray(arr)) throw new TomlError(`'${leaf}' redefined as array of tables`);
      const entry: TomlTable = {};
      (arr as TomlValue[]).push(entry);
      return entry;
    }
    const existing = node[leaf];
    if (existing === undefined) {
      const t: TomlTable = {};
      node[leaf] = t;
      return t;
    }
    if (isPlainObject(existing)) return existing;
    throw new TomlError(`'${leaf}' redefined as table`);
  }

  /** Descend one key segment, following the last element of an array-of-tables. */
  private descend(node: TomlTable, key: string): TomlTable {
    let child = node[key];
    if (child === undefined) {
      child = {};
      node[key] = child;
      return child as TomlTable;
    }
    if (Array.isArray(child)) {
      const last = child[child.length - 1];
      if (!isPlainObject(last)) throw new TomlError(`cannot descend into '${key}'`);
      return last;
    }
    if (isPlainObject(child)) return child;
    throw new TomlError(`cannot descend into scalar '${key}'`);
  }

  private parseAssignment(current: TomlTable): void {
    const path = this.parseKeyPath();
    this.skipInlineWs();
    if (this.peek() !== "=") throw new TomlError(`expected '=' after key '${path.join(".")}'`);
    this.i++;
    this.skipInlineWs();
    const value = this.parseValue();
    let node = current;
    for (let k = 0; k < path.length - 1; k++) {
      const next = node[path[k]];
      if (next === undefined) {
        const t: TomlTable = {};
        node[path[k]] = t;
        node = t;
      } else if (isPlainObject(next)) {
        node = next;
      } else {
        throw new TomlError(`dotted key '${path.join(".")}' traverses a non-table`);
      }
    }
    node[path[path.length - 1]] = value;
  }

  private parseKeyPath(): string[] {
    const segs: string[] = [this.parseKeySegment()];
    for (;;) {
      this.skipInlineWs();
      if (this.peek() !== ".") break;
      this.i++;
      this.skipInlineWs();
      segs.push(this.parseKeySegment());
    }
    return segs;
  }

  private parseKeySegment(): string {
    const c = this.peek();
    if (c === '"' || c === "'") return this.parseString();
    let out = "";
    while (!this.eof()) {
      const ch = this.peek();
      if (/[A-Za-z0-9_-]/.test(ch)) {
        out += ch;
        this.i++;
      } else {
        break;
      }
    }
    if (!out) throw new TomlError("empty or invalid key");
    return out;
  }

  private parseValue(): TomlValue {
    const c = this.peek();
    if (c === '"' || c === "'") {
      if (this.s.startsWith(c.repeat(3), this.i)) {
        throw new TomlError("multi-line strings are not supported by the visual editor");
      }
      return this.parseString();
    }
    if (c === "[") return this.parseArray();
    if (c === "{") return this.parseInlineTable();
    return this.parseBareValue();
  }

  private parseString(): string {
    const quote = this.peek();
    this.i++; // opening quote
    let out = "";
    if (quote === "'") {
      // literal string — no escapes
      while (!this.eof() && this.peek() !== "'") {
        if (this.peek() === "\n") throw new TomlError("unterminated string");
        out += this.peek();
        this.i++;
      }
      if (this.eof()) throw new TomlError("unterminated string");
      this.i++; // closing quote
      return out;
    }
    while (!this.eof() && this.peek() !== '"') {
      const ch = this.peek();
      if (ch === "\n") throw new TomlError("unterminated string");
      if (ch === "\\") {
        this.i++;
        const esc = this.peek();
        switch (esc) {
          case "n": out += "\n"; break;
          case "t": out += "\t"; break;
          case "r": out += "\r"; break;
          case '"': out += '"'; break;
          case "\\": out += "\\"; break;
          case "b": out += "\b"; break;
          case "f": out += "\f"; break;
          case "u":
          case "U": {
            const len = esc === "u" ? 4 : 8;
            const hex = this.s.slice(this.i + 1, this.i + 1 + len);
            if (hex.length !== len || !/^[0-9A-Fa-f]+$/.test(hex)) {
              throw new TomlError("invalid unicode escape");
            }
            out += String.fromCodePoint(parseInt(hex, 16));
            this.i += len;
            break;
          }
          default:
            throw new TomlError(`invalid escape '\\${esc}'`);
        }
        this.i++;
      } else {
        out += ch;
        this.i++;
      }
    }
    if (this.eof()) throw new TomlError("unterminated string");
    this.i++; // closing quote
    return out;
  }

  private parseArray(): TomlValue[] {
    this.i++; // '['
    const arr: TomlValue[] = [];
    for (;;) {
      this.skipTrivia();
      if (this.eof()) throw new TomlError("unterminated array");
      if (this.peek() === "]") {
        this.i++;
        return arr;
      }
      arr.push(this.parseValue());
      this.skipTrivia();
      if (this.peek() === ",") {
        this.i++;
      } else if (this.peek() === "]") {
        this.i++;
        return arr;
      } else {
        throw new TomlError("expected ',' or ']' in array");
      }
    }
  }

  private parseInlineTable(): TomlTable {
    this.i++; // '{'
    const table: TomlTable = {};
    this.skipInlineWs();
    if (this.peek() === "}") {
      this.i++;
      return table;
    }
    for (;;) {
      this.skipInlineWs();
      const path = this.parseKeyPath();
      this.skipInlineWs();
      if (this.peek() !== "=") throw new TomlError("expected '=' in inline table");
      this.i++;
      this.skipInlineWs();
      const value = this.parseValue();
      let node = table;
      for (let k = 0; k < path.length - 1; k++) {
        const next = node[path[k]];
        if (isPlainObject(next)) {
          node = next;
        } else {
          const t: TomlTable = {};
          node[path[k]] = t;
          node = t;
        }
      }
      node[path[path.length - 1]] = value;
      this.skipInlineWs();
      if (this.peek() === ",") {
        this.i++;
        continue;
      }
      if (this.peek() === "}") {
        this.i++;
        return table;
      }
      throw new TomlError("expected ',' or '}' in inline table");
    }
  }

  private parseBareValue(): TomlValue {
    let raw = "";
    while (!this.eof()) {
      const ch = this.peek();
      if (ch === "," || ch === "]" || ch === "}" || ch === "\n" || ch === "#" || ch === "\r") break;
      raw += ch;
      this.i++;
    }
    raw = raw.trim();
    if (raw === "true") return true;
    if (raw === "false") return false;
    if (raw === "") throw new TomlError("empty value");
    // Reject date/time-looking bare values (unsupported) → fail safe.
    if (/[T:]/.test(raw) || /^\d{4}-\d{2}-\d{2}/.test(raw)) {
      throw new TomlError("date/time values are not supported by the visual editor");
    }
    const num = Number(raw.replace(/_/g, ""));
    if (!Number.isNaN(num) && /^[+-]?[0-9._eE+-]+$/.test(raw)) return num;
    throw new TomlError(`unquoted value '${raw}' is not supported`);
  }
}

export function parseToml(input: string): TomlTable {
  return new Scanner(input).parse();
}

// ─── Serializer ──────────────────────────────────────────────────────────────

function isTableArray(v: TomlValue): v is TomlTable[] {
  return Array.isArray(v) && v.length > 0 && v.every(isPlainObject);
}

function formatString(s: string): string {
  const escaped = s
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\n/g, "\\n")
    .replace(/\t/g, "\\t")
    .replace(/\r/g, "\\r");
  return `"${escaped}"`;
}

function formatInline(v: TomlValue): string {
  if (typeof v === "string") return formatString(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) return `[${v.map(formatInline).join(", ")}]`;
  // inline table
  const pairs = Object.entries(v).map(([k, val]) => `${formatKey(k)} = ${formatInline(val)}`);
  return `{ ${pairs.join(", ")} }`;
}

function formatKey(k: string): string {
  return /^[A-Za-z0-9_-]+$/.test(k) ? k : formatString(k);
}

function emitTable(path: string[], table: TomlTable, out: string[]): void {
  const leaves: [string, TomlValue][] = [];
  const subs: [string, TomlValue][] = [];
  for (const [k, v] of Object.entries(table)) {
    if (isPlainObject(v) || isTableArray(v)) subs.push([k, v]);
    else leaves.push([k, v]);
  }
  if (path.length > 0 && (leaves.length > 0 || subs.length === 0)) {
    out.push(`[${path.map(formatKey).join(".")}]`);
  }
  for (const [k, v] of leaves) {
    out.push(`${formatKey(k)} = ${formatInline(v)}`);
  }
  for (const [k, v] of subs) {
    const childPath = [...path, k];
    if (isTableArray(v)) {
      for (const entry of v) {
        out.push("");
        out.push(`[[${childPath.map(formatKey).join(".")}]]`);
        emitEntryBody(childPath, entry, out);
      }
    } else {
      out.push("");
      emitTable(childPath, v as TomlTable, out);
    }
  }
}

/** Body of an array-of-tables element (header already emitted). */
function emitEntryBody(path: string[], table: TomlTable, out: string[]): void {
  const leaves: [string, TomlValue][] = [];
  const subs: [string, TomlValue][] = [];
  for (const [k, v] of Object.entries(table)) {
    if (isPlainObject(v) || isTableArray(v)) subs.push([k, v]);
    else leaves.push([k, v]);
  }
  for (const [k, v] of leaves) {
    out.push(`${formatKey(k)} = ${formatInline(v)}`);
  }
  for (const [k, v] of subs) {
    const childPath = [...path, k];
    if (isTableArray(v)) {
      for (const entry of v) {
        out.push("");
        out.push(`[[${childPath.map(formatKey).join(".")}]]`);
        emitEntryBody(childPath, entry, out);
      }
    } else {
      out.push("");
      emitTable(childPath, v as TomlTable, out);
    }
  }
}

export function serializeToml(table: TomlTable): string {
  const out: string[] = [];
  emitTable([], table, out);
  // Collapse a leading blank line and trailing whitespace; end with newline.
  const text = out.join("\n").replace(/^\n+/, "").replace(/\s+$/, "");
  return text.length > 0 ? text + "\n" : "";
}

// ─── Round-trip verification ─────────────────────────────────────────────────

export function deepEqual(a: TomlValue, b: TomlValue): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((x, i) => deepEqual(x, b[i]));
  }
  if (isPlainObject(a) && isPlainObject(b)) {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => k in b && deepEqual(a[k], b[k]));
  }
  return false;
}

/**
 * Prove a manifest can be safely edited in the form: parse → serialize →
 * parse and confirm the value tree is stable. On any parse failure or drift
 * the caller keeps the user in the raw editor. This is the safety net that
 * lets the scoped codec never silently corrupt a manifest.
 */
export function roundTripSafe(input: string): { safe: boolean; reason?: string } {
  let first: TomlTable;
  try {
    first = parseToml(input);
  } catch (e) {
    return { safe: false, reason: e instanceof Error ? e.message : "parse failed" };
  }
  try {
    const again = parseToml(serializeToml(first));
    if (!deepEqual(first, again)) {
      return { safe: false, reason: "the manifest uses TOML this editor can't round-trip" };
    }
  } catch (e) {
    return { safe: false, reason: e instanceof Error ? e.message : "serialization failed" };
  }
  return { safe: true };
}
