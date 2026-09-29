"use client";

import { useMutation } from "@apollo/client/react";
import { useState } from "react";
import { toast } from "sonner";

import { IMPORT_SKILLS_FROM_REPO } from "@/graphql/agents/agents.mutations";

import { type FieldErrors, hasErrors, splitErrors } from "./catalog";

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

export type ImportField = "repoUrl" | "branch";

/** Why an import was refused: beside its field where the server named one (spec 44 §5.4). */
export type ImportErrors = FieldErrors<ImportField>;

/**
 * Imports skills and tools from a repo's astrolift.toml and keeps what it
 * imported. Refusals come back as errors for the sheet to show in place; the
 * only toast is the outcome. The data half of ImportSkillsSheet.
 */
export function useImportSkills() {
  const [result, setResult] = useState<ImportResult | null>(null);

  const [importMutation, { loading }] = useMutation<ImportSkillsData>(IMPORT_SKILLS_FROM_REPO, {
    refetchQueries: ["ListSkills"],
  });

  /** Resolves the errors to show; empty when the import went through. */
  async function importSkills(repoUrl: string, branch: string): Promise<ImportErrors> {
    if (!repoUrl.trim()) return { repoUrl: "Enter the repository URL." };
    setResult(null);
    try {
      const { data } = await importMutation({
        variables: { repoUrl: repoUrl.trim(), branch: branch.trim() || "main" },
      });
      const payload = data?.importSkillsFromRepo;
      if (payload?.ok && payload.data) {
        setResult(payload.data);
        toast.success(`Imported from ${payload.data.sourceRef}`);
        return {};
      }
      const errors = splitErrors(payload?.errors ?? [], ["repoUrl", "branch"] as const);
      return hasErrors(errors) ? errors : { form: "The import was refused." };
    } catch (err) {
      return { form: err instanceof Error ? err.message : String(err) };
    }
  }

  return {
    loading,
    result,
    importSkills,
    clearResult: () => setResult(null),
  };
}

export type ImportSkillsState = ReturnType<typeof useImportSkills>;
