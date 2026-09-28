"use client";

import Link from "next/link";
import { BoxIcon, Loader2Icon, WrenchIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

import type { ToolRegistryTool, useToolRegistry } from "./use-tool-registry";

export type ToolRegistryScreenProps = ReturnType<typeof useToolRegistry>;

const ADAPTER_LABELS: Record<string, string> = {
  python_fn: "Python function",
  http_endpoint: "HTTP endpoint",
  mcp_server: "MCP server",
};

const ADAPTER_ORDER = ["python_fn", "http_endpoint", "mcp_server", "unknown"];

/** Agents › Tools: every tool definition in the org, grouped by adapter. */
export function ToolRegistryScreen({ tools, loading, error }: ToolRegistryScreenProps) {
  // Group by adapter
  const byAdapter = tools.reduce<Record<string, ToolRegistryTool[]>>((acc, t) => {
    const key = t.adapter || "unknown";
    if (!acc[key]) acc[key] = [];
    acc[key].push(t);
    return acc;
  }, {});

  const sortedAdapters = Object.keys(byAdapter).sort(
    (a, b) => ADAPTER_ORDER.indexOf(a) - ADAPTER_ORDER.indexOf(b)
  );

  return (
    <PageShell
      title="Tool registry"
      description="All tool definitions across every skill in this org. Tools are registered on the skill builder page."
    >
      {loading && tools.length === 0 && (
        <div className="flex items-center justify-center p-12">
          <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
        </div>
      )}

      {error && (
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
          Error: {error.message}
        </div>
      )}

      {!loading && !error && tools.length === 0 && (
        <EmptyState
          icon={<BoxIcon className="size-5" />}
          title="No tools registered"
          description="Register tool definitions on a skill's builder page. Tools declare the callable capabilities agents can invoke."
          actionHref="/agents/skills"
          actionLabel="Go to skills"
        />
      )}

      {sortedAdapters.map((adapter) => (
        <section key={adapter} className="flex flex-col gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <Badge variant="outline" className="text-xs">
              {ADAPTER_LABELS[adapter] ?? adapter}
            </Badge>
            <span className="text-muted-foreground text-xs">
              {byAdapter[adapter].length} {byAdapter[adapter].length === 1 ? "tool" : "tools"}
            </span>
          </h2>
          <div className="flex flex-col gap-2">
            {byAdapter[adapter].map((tool) => (
              <Link
                key={tool.id}
                href={`/agents/tools/${tool.id}`}
                className="hover:bg-muted/50 flex items-center gap-4 rounded-lg border px-4 py-3 transition-colors"
              >
                <div className="bg-muted rounded-md p-1.5">
                  <WrenchIcon className="text-muted-foreground h-4 w-4" />
                </div>

                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="font-medium">{tool.name}</span>
                  {tool.description && (
                    <span className="text-muted-foreground truncate text-sm">
                      {tool.description}
                    </span>
                  )}
                </div>

                {tool.handlerRef && (
                  <span className="text-muted-foreground max-w-[200px] shrink-0 truncate font-mono text-xs">
                    {tool.handlerRef}
                  </span>
                )}

                <span className="text-muted-foreground shrink-0 font-mono text-xs">
                  {tool.slug}
                </span>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </PageShell>
  );
}
