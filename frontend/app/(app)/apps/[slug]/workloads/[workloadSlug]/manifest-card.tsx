"use client";

import { useQuery } from "@apollo/client/react";
import { AlertTriangleIcon, FileCodeIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_WORKLOAD_MANIFEST } from "@/graphql/registry/registry.queries";
import { cn } from "@/lib/utils";

interface K8sResource {
  apiVersion: string;
  kind: string;
  metadata: { name: string; [k: string]: unknown };
  [k: string]: unknown;
}

interface WorkloadManifest {
  appSlug: string;
  workloadSlug: string;
  environmentName: string;
  imageTag: string;
  namespace: string;
  resources: K8sResource[];
  previousImageTag: string;
  previousDeploymentId: string;
  resourcesPrevious: K8sResource[];
  error: string | null;
  errorPath: string | null;
  errorLine: number | null;
  errorColumn: number | null;
}

interface ManifestResp {
  astroliftWorkloadManifest: WorkloadManifest | null;
}

export interface ManifestCardLabels {
  title: string;
  description: string;
  viewToggleManifest: string;
  viewToggleDiff: string;
  noResources: string;
  errorTitle: string;
  diffEmpty: string;
  diffPreviousLabel: string;
  diffCurrentLabel: string;
  diffNoHistory: string;
}

export function ManifestCard({
  appSlug,
  workloadSlug,
  environmentName,
  labels,
}: {
  appSlug: string;
  workloadSlug: string;
  environmentName: string | null;
  labels: ManifestCardLabels;
}) {
  const { data, loading } = useQuery<ManifestResp>(GET_WORKLOAD_MANIFEST, {
    variables: { appSlug, workloadSlug, environmentName, imageTag: null },
    fetchPolicy: "cache-and-network",
  });

  const [mode, setMode] = React.useState<"manifest" | "diff">("manifest");

  const manifest = data?.astroliftWorkloadManifest ?? null;

  if (loading && !manifest) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{labels.title}</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-48 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (!manifest) return null;

  const hasPrior = manifest.previousImageTag !== "";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <FileCodeIcon className="size-4" />
            {labels.title}
          </CardTitle>
          <div className="flex items-center gap-1 rounded-md border p-0.5">
            <Button
              size="sm"
              variant={mode === "manifest" ? "secondary" : "ghost"}
              className="h-7 px-2 text-xs"
              onClick={() => setMode("manifest")}
            >
              {labels.viewToggleManifest}
            </Button>
            <Button
              size="sm"
              variant={mode === "diff" ? "secondary" : "ghost"}
              className="h-7 px-2 text-xs"
              onClick={() => setMode("diff")}
              disabled={!hasPrior}
              title={!hasPrior ? labels.diffNoHistory : undefined}
            >
              {labels.viewToggleDiff}
            </Button>
          </div>
        </div>
        <p className="text-muted-foreground mt-1 text-xs">{labels.description}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="secondary">
            env <span className="ml-1 font-mono">{manifest.environmentName}</span>
          </Badge>
          <Badge variant="secondary">
            ns <span className="ml-1 font-mono">{manifest.namespace}</span>
          </Badge>
          <Badge variant="secondary">
            image <span className="ml-1 font-mono">{manifest.imageTag}</span>
          </Badge>
          {hasPrior && mode === "diff" && (
            <Badge variant="outline" className="font-mono">
              prev: {manifest.previousImageTag}
            </Badge>
          )}
          <Badge variant="outline">
            {manifest.resources.length} resource
            {manifest.resources.length === 1 ? "" : "s"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {manifest.error ? (
          <ManifestError
            error={manifest.error}
            errorPath={manifest.errorPath}
            errorLine={manifest.errorLine}
            errorColumn={manifest.errorColumn}
            label={labels.errorTitle}
          />
        ) : manifest.resources.length === 0 ? (
          <p className="text-muted-foreground text-sm">{labels.noResources}</p>
        ) : mode === "manifest" ? (
          <ManifestList resources={manifest.resources} />
        ) : (
          <ManifestDiff
            current={manifest.resources}
            previous={manifest.resourcesPrevious}
            labels={labels}
          />
        )}
      </CardContent>
    </Card>
  );
}

function ManifestList({ resources }: { resources: K8sResource[] }) {
  return (
    <div className="space-y-3">
      {resources.map((resource, i) => (
        <div key={`${resource.kind}-${resource.metadata.name}-${i}`}>
          <div className="text-muted-foreground mb-1 flex items-center gap-2 text-xs">
            <span className="font-mono font-semibold">{resource.kind}</span>
            <span className="font-mono">{resource.metadata.name}</span>
            <span className="font-mono text-[10px]">{resource.apiVersion}</span>
          </div>
          <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs">
            {toYaml(resource)}
          </pre>
        </div>
      ))}
    </div>
  );
}

function ManifestDiff({
  current,
  previous,
  labels,
}: {
  current: K8sResource[];
  previous: K8sResource[];
  labels: ManifestCardLabels;
}) {
  // Pair resources by (kind, name) so a rename shows as add+remove
  // rather than a confusing inline diff.
  const byKey = new Map<string, { key: string; previous?: K8sResource; current?: K8sResource }>();
  for (const r of previous) {
    const key = `${r.kind}/${r.metadata.name}`;
    byKey.set(key, { key, previous: r });
  }
  for (const r of current) {
    const key = `${r.kind}/${r.metadata.name}`;
    const existing = byKey.get(key);
    if (existing) existing.current = r;
    else byKey.set(key, { key, current: r });
  }

  // Compute per-resource diff once so the render path is cheap.
  const entries = Array.from(byKey.values()).map((entry) => {
    const previousYaml = entry.previous ? toYaml(entry.previous) : "";
    const currentYaml = entry.current ? toYaml(entry.current) : "";
    const hunks = unifiedDiff(previousYaml, currentYaml);
    return { ...entry, previousYaml, currentYaml, hunks };
  });

  const anyDiff = entries.some((e) => e.hunks.changed);
  if (!anyDiff) {
    return <p className="text-muted-foreground text-sm">{labels.diffEmpty}</p>;
  }

  return (
    <div className="space-y-4">
      {entries
        .filter((e) => e.hunks.changed)
        .map((entry) => (
          <div key={entry.key}>
            <div className="text-muted-foreground mb-1 flex items-center gap-2 text-xs">
              <span className="font-mono font-semibold">{entry.key}</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              <div>
                <div className="text-muted-foreground mb-1 text-[10px] tracking-wide uppercase">
                  {labels.diffPreviousLabel}
                </div>
                <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs">
                  {renderDiffSide(entry.previousYaml, entry.hunks.lines, "prev")}
                </pre>
              </div>
              <div>
                <div className="text-muted-foreground mb-1 text-[10px] tracking-wide uppercase">
                  {labels.diffCurrentLabel}
                </div>
                <pre className="bg-muted max-h-96 overflow-auto rounded-md p-3 font-mono text-xs">
                  {renderDiffSide(entry.currentYaml, entry.hunks.lines, "cur")}
                </pre>
              </div>
            </div>
          </div>
        ))}
    </div>
  );
}

function ManifestError({
  error,
  errorPath,
  errorLine,
  errorColumn,
  label,
}: {
  error: string;
  errorPath: string | null;
  errorLine: number | null;
  errorColumn: number | null;
  label: string;
}) {
  return (
    <div className="space-y-2">
      <div className="text-destructive flex items-center gap-2 text-sm font-medium">
        <AlertTriangleIcon className="size-4" />
        {label}
      </div>
      <pre className="bg-muted overflow-auto rounded-md p-3 text-xs">{error}</pre>
      {errorPath && (
        <p className="text-muted-foreground text-xs">
          path: <span className="font-mono">{errorPath}</span>
          {errorLine && (
            <>
              {" "}
              — line <span className="font-mono">{errorLine}</span>
              {errorColumn && (
                <>
                  , col <span className="font-mono">{errorColumn}</span>
                </>
              )}
            </>
          )}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// YAML serializer — small, dependency-free. Targets the strict subset
// of values that appear in a rendered K8s manifest (strings, numbers,
// booleans, nulls, arrays, objects). Stable key ordering matches the
// renderer's sort_keys so the diff doesn't churn on cosmetic reorders.
// ---------------------------------------------------------------------------

function toYaml(value: unknown, indent: number = 0): string {
  return yamlValue(value, indent).trimEnd() + "\n";
}

function yamlValue(value: unknown, indent: number): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return String(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return yamlString(value);
  if (Array.isArray(value)) return yamlArray(value, indent);
  if (typeof value === "object") return yamlObject(value as Record<string, unknown>, indent);
  return JSON.stringify(value);
}

function yamlObject(obj: Record<string, unknown>, indent: number): string {
  const keys = Object.keys(obj);
  if (keys.length === 0) return "{}";
  const pad = " ".repeat(indent);
  const out: string[] = [];
  for (const key of keys) {
    const v = obj[key];
    const child = yamlValue(v, indent + 2);
    if (typeof v === "object" && v !== null && !isEmpty(v)) {
      // Object / array — child on next line with deeper indent.
      out.push(`${pad}${key}:\n${child}`);
    } else {
      // Scalar — inline.
      out.push(`${pad}${key}: ${child}`);
    }
  }
  return out.join("\n");
}

function yamlArray(arr: unknown[], indent: number): string {
  if (arr.length === 0) return "[]";
  const pad = " ".repeat(indent);
  const out: string[] = [];
  for (const item of arr) {
    if (typeof item === "object" && item !== null && !isEmpty(item)) {
      // The child block needs to start indented under the dash. We
      // render the child block then re-indent the FIRST line to sit
      // on the same line as the dash.
      const child = yamlValue(item, indent + 2);
      const lines = child.split("\n");
      const head = lines[0].trimStart();
      const tail = lines.slice(1).join("\n");
      out.push(`${pad}- ${head}${tail ? "\n" + tail : ""}`);
    } else {
      out.push(`${pad}- ${yamlValue(item, indent + 2)}`);
    }
  }
  return out.join("\n");
}

function yamlString(s: string): string {
  // Quote when the string has YAML-special characters or could be
  // interpreted as something other than a string (numbers, bools,
  // nulls, etc.). The K8s manifest renderer mostly emits safe
  // identifiers and quoted versions; double-quote anything that
  // doesn't pass the plain-scalar sniff test.
  if (s === "") return '""';
  if (/^[A-Za-z_][A-Za-z0-9._/-]*$/.test(s) && !RESERVED.has(s.toLowerCase())) {
    return s;
  }
  return JSON.stringify(s); // JSON quoting is a valid subset of YAML double-quoting.
}

const RESERVED = new Set(["true", "false", "null", "yes", "no", "on", "off", "y", "n"]);

function isEmpty(v: unknown): boolean {
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object" && v !== null) return Object.keys(v).length === 0;
  return false;
}

// ---------------------------------------------------------------------------
// Unified diff — line-by-line LCS.
// ---------------------------------------------------------------------------

interface DiffLine {
  // 'eq' = same in both, 'add' = only in current, 'rm' = only in
  // previous. We render eq on both sides and add/rm only on their
  // respective side (with a placeholder on the other).
  type: "eq" | "add" | "rm";
  text: string;
}

function unifiedDiff(
  prev: string,
  cur: string
): {
  changed: boolean;
  lines: DiffLine[];
} {
  const a = prev.split("\n");
  const b = cur.split("\n");
  // Classic LCS table for line-level diff. Small inputs (≤ a few
  // hundred lines per resource) so O(nm) is fine.
  const dp: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array(b.length + 1).fill(0)
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      if (a[i] === b[j]) dp[i][j] = dp[i + 1][j + 1] + 1;
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ type: "eq", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      lines.push({ type: "rm", text: a[i] });
      i++;
    } else {
      lines.push({ type: "add", text: b[j] });
      j++;
    }
  }
  while (i < a.length) {
    lines.push({ type: "rm", text: a[i++] });
  }
  while (j < b.length) {
    lines.push({ type: "add", text: b[j++] });
  }
  const changed = lines.some((l) => l.type !== "eq");
  return { changed, lines };
}

function renderDiffSide(full: string, lines: DiffLine[], side: "prev" | "cur"): React.ReactNode {
  // Render the side-appropriate lines with colour: red for rm on the
  // prev side, green for add on the cur side, grey for eq.
  void full; // diff payload comes from `lines` already
  return (
    <>
      {lines.map((line, idx) => {
        const visible =
          line.type === "eq" ||
          (side === "prev" && line.type === "rm") ||
          (side === "cur" && line.type === "add");
        if (!visible) {
          // Render a blank placeholder so the two sides stay
          // line-aligned — easier scanning than a tight crash.
          return <div key={idx}>&nbsp;</div>;
        }
        const cls = cn(
          line.type === "rm" && "bg-rose-500/10 text-rose-700 dark:text-rose-300",
          line.type === "add" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
        );
        return (
          <div key={idx} className={cls}>
            {line.text || " "}
          </div>
        );
      })}
    </>
  );
}
