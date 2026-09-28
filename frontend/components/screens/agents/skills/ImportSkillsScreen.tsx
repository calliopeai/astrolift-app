"use client";

import { CheckCircle2Icon, GitBranchIcon, Loader2Icon } from "lucide-react";
import { useState } from "react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { ImportSkillsState } from "./use-import-skills";

export type ImportSkillsScreenProps = ImportSkillsState;

/**
 * Import skills from a repo's astrolift.toml. Holds the repo URL and
 * branch fields; useImportSkills runs the import and keeps its result.
 */
export function ImportSkillsScreen({
  loading,
  result,
  importSkills,
  clearResult,
  viewSkills,
}: ImportSkillsScreenProps) {
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    await importSkills(repoUrl, branch);
  }

  return (
    <PageShell
      title="Import skills from repo"
      description="Fetch a GitHub repository's astrolift.toml and upsert the declared skills and tools into this org."
    >
      <form onSubmit={handleSubmit} className="flex max-w-2xl flex-col gap-6">
        <div className="flex flex-col gap-1.5">
          <Label>Repository URL</Label>
          <Input
            value={repoUrl}
            placeholder="https://github.com/owner/agent-config"
            className="font-mono"
            onChange={(e) => setRepoUrl(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            Must be a github.com repository. The repo must contain an
            <span className="font-mono"> astrolift.toml</span> at its root.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Branch / ref</Label>
          <Input
            value={branch}
            placeholder="main"
            className="max-w-48 font-mono"
            onChange={(e) => setBranch(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-2">
          <Button type="submit" disabled={loading || !repoUrl.trim()}>
            {loading ? (
              <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <GitBranchIcon className="mr-2 h-4 w-4" />
            )}
            {loading ? "Importing..." : "Import"}
          </Button>
        </div>
      </form>

      {result && (
        <Card className="max-w-2xl">
          <CardContent>
            <div className="mb-3 flex items-center gap-2">
              <CheckCircle2Icon className="text-success-fg h-4 w-4" />
              <span className="text-sm font-medium">Import complete</span>
              <Badge variant="outline" className="font-mono text-xs">
                {result.sourceRef}
              </Badge>
            </div>
            <div className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                  Skills ({result.importedSkills.length})
                </p>
                {result.importedSkills.length === 0 ? (
                  <p className="text-muted-foreground text-xs italic">none</p>
                ) : (
                  <ul className="flex flex-col gap-0.5">
                    {result.importedSkills.map((s) => (
                      <li key={s} className="font-mono text-xs">
                        {s}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div>
                <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                  Tools ({result.importedTools.length})
                </p>
                {result.importedTools.length === 0 ? (
                  <p className="text-muted-foreground text-xs italic">none</p>
                ) : (
                  <ul className="flex flex-col gap-0.5">
                    {result.importedTools.map((t) => (
                      <li key={t} className="font-mono text-xs">
                        {t}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              <Button size="sm" variant="outline" onClick={viewSkills}>
                View skills
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  clearResult();
                  setRepoUrl("");
                }}
              >
                Import another
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </PageShell>
  );
}
