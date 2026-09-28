"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { IMPORT_SKILLS_FROM_REPO } from "@/graphql/agents/agents.mutations";

type MutError = { field: string; message: string; code: string };

export type ImportResult = {
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

/** Imports skills and tools from a repo's astrolift.toml. The data half of ImportSkillsScreen. */
export function useImportSkills() {
  const router = useRouter();
  const [result, setResult] = useState<ImportResult | null>(null);

  const [importMutation, { loading }] = useMutation<ImportSkillsData>(IMPORT_SKILLS_FROM_REPO, {
    refetchQueries: ["ListSkills"],
  });

  async function importSkills(repoUrl: string, branch: string) {
    if (!repoUrl.trim()) {
      toast.error("Repository URL is required");
      return;
    }
    setResult(null);
    const { data } = await importMutation({
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

  return {
    loading,
    result,
    importSkills,
    clearResult: () => setResult(null),
    viewSkills: () => router.push("/agents/skills"),
  };
}

export type ImportSkillsState = ReturnType<typeof useImportSkills>;
