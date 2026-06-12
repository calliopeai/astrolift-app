"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@apollo/client/react";
import {
  CheckCircle2Icon,
  GitBranchIcon,
  Loader2Icon,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageShell } from "@/components/PageShell";
import { IMPORT_SKILLS_FROM_REPO } from "@/graphql/agents/agents.mutations";

// ─── Types ────────────────────────────────────────────────────────────────────

type MutError = { field: string; message: string; code: string };

type ImportResult = {
  importedSkills: string[];
  importedTools: string[];
  sourceRef: string;
};

type ImportSkillsData = {
  importSkillsFromRepo: {
    ok: boolean;
    errors: MutError[];
    data: ImportResult | null;
  };
};

export default function ImportSkillsPage() {
  const router = useRouter();
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [result, setResult] = useState<ImportResult | null>(null);

  const [importSkills, { loading }] = useMutation<ImportSkillsData>(IMPORT_SKILLS_FROM_REPO, {
    refetchQueries: ["ListSkills"],
  });

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!repoUrl.trim()) {
      toast.error("Repository URL is required");
      return;
    }
    setResult(null);
    const { data } = await importSkills({
      variables: {
        repoUrl: repoUrl.trim(),
        branch: branch.trim() || "main",
      },
    });
    if (data?.importSkillsFromRepo?.ok && data.importSkillsFromRepo.data) {
      setResult(data.importSkillsFromRepo.data);
      toast.success(`Imported from ${data.importSkillsFromRepo.data.sourceRef}`);
    } else {
      for (const err of data?.importSkillsFromRepo?.errors ?? []) {
        toast.error(`${err.field}: ${err.message}`);
      }
    }
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
        <div className="max-w-2xl rounded-lg border p-4">
          <div className="mb-3 flex items-center gap-2">
            <CheckCircle2Icon className="h-4 w-4 text-green-500" />
            <span className="text-sm font-medium">Import complete</span>
            <Badge variant="outline" className="font-mono text-xs">
              {result.sourceRef}
            </Badge>
          </div>
          <div className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <p className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wide">
                Skills ({result.importedSkills.length})
              </p>
              {result.importedSkills.length === 0 ? (
                <p className="text-muted-foreground italic text-xs">none</p>
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
              <p className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wide">
                Tools ({result.importedTools.length})
              </p>
              {result.importedTools.length === 0 ? (
                <p className="text-muted-foreground italic text-xs">none</p>
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
            <Button
              size="sm"
              variant="outline"
              onClick={() => router.push("/agents/skills")}
            >
              View skills
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => { setResult(null); setRepoUrl(""); }}
            >
              Import another
            </Button>
          </div>
        </div>
      )}
    </PageShell>
  );
}
