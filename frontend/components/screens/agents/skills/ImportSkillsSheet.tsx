"use client";

import { AlertTriangleIcon, CheckCircle2Icon, GitBranchIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import type { ImportErrors, ImportSkillsState } from "./use-import-skills";

export type ImportSkillsSheetProps = ImportSkillsState & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Errors to open with; stories use it. */
  initialErrors?: ImportErrors;
};

/**
 * Import skills from a repo's astrolift.toml: two fields, so a sheet over the
 * Skills list (spec 44 §5.4). Errors stand beside their fields; the outcome
 * is a toast and the result stays in the sheet. Pure view; the data half is
 * useImportSkills.
 */
export function ImportSkillsSheet({
  open,
  onOpenChange,
  loading,
  result,
  importSkills,
  clearResult,
  initialErrors = {},
}: ImportSkillsSheetProps) {
  const tr = useTranslations("agentSkills.import");
  const common = useTranslations("agentSkills.common");
  const [repoUrl, setRepoUrl] = React.useState("");
  const [branch, setBranch] = React.useState("main");
  const [errors, setErrors] = React.useState<ImportErrors>(initialErrors);

  function close(next: boolean) {
    onOpenChange(next);
    if (!next) {
      clearResult();
      setRepoUrl("");
      setBranch("main");
      setErrors({});
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErrors(await importSkills(repoUrl, branch));
  }

  return (
    <Sheet open={open} onOpenChange={close}>
      <SheetContent className="flex flex-col overflow-y-auto">
        <SheetHeader>
          <SheetTitle>{tr("title")}</SheetTitle>
          <SheetDescription>{tr("description")}</SheetDescription>
        </SheetHeader>

        {result ? (
          <div className="flex min-w-0 flex-1 flex-col gap-4 px-4">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <CheckCircle2Icon className="text-success-fg size-4 shrink-0" aria-hidden />
              <span className="text-sm font-medium">{tr("complete")}</span>
              <Badge variant="outline" className="max-w-full min-w-0 font-mono text-xs">
                <span className="min-w-0 truncate" title={result.sourceRef}>
                  {result.sourceRef}
                </span>
              </Badge>
            </div>
            <ImportedList title={common("skills")} items={result.importedSkills} />
            <ImportedList title={common("tools")} items={result.importedTools} />
            <SheetFooter className="px-0">
              <Button
                variant="outline"
                onClick={() => {
                  clearResult();
                  setRepoUrl("");
                }}
              >
                {tr("importAnother")}
              </Button>
              <Button onClick={() => close(false)}>{tr("done")}</Button>
            </SheetFooter>
          </div>
        ) : (
          <form
            onSubmit={submit}
            noValidate
            className="flex min-w-0 flex-1 flex-col gap-4 px-4 pb-4"
          >
            {errors.form && (
              <div
                role="alert"
                className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
              >
                <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
                <span className="min-w-0 [overflow-wrap:anywhere]">{errors.form}</span>
              </div>
            )}
            <Field data-invalid={Boolean(errors.repoUrl) || undefined}>
              <FieldLabel htmlFor="import-repo">{tr("repoUrl")}</FieldLabel>
              <Input
                id="import-repo"
                value={repoUrl}
                placeholder="https://github.com/owner/agent-config"
                className="font-mono"
                spellCheck={false}
                autoFocus
                aria-invalid={Boolean(errors.repoUrl) || undefined}
                onChange={(e) => {
                  setRepoUrl(e.target.value);
                  setErrors((x) => ({ ...x, repoUrl: undefined }));
                }}
              />
              <FieldError className="[overflow-wrap:anywhere]">{errors.repoUrl}</FieldError>
              <FieldDescription>
                {tr.rich("repoHelp", {
                  code: (chunks) => <span className="font-mono">{chunks}</span>,
                })}
              </FieldDescription>
            </Field>
            <Field data-invalid={Boolean(errors.branch) || undefined}>
              <FieldLabel htmlFor="import-branch">{tr("branchRef")}</FieldLabel>
              <Input
                id="import-branch"
                value={branch}
                placeholder="main"
                className="max-w-48 font-mono"
                spellCheck={false}
                aria-invalid={Boolean(errors.branch) || undefined}
                onChange={(e) => {
                  setBranch(e.target.value);
                  setErrors((x) => ({ ...x, branch: undefined }));
                }}
              />
              <FieldError className="[overflow-wrap:anywhere]">{errors.branch}</FieldError>
            </Field>
            <SheetFooter className="mt-auto px-0">
              <Button type="submit" disabled={loading || !repoUrl.trim()}>
                {loading ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <GitBranchIcon className="size-4" />
                )}
                {loading ? tr("importing") : tr("import")}
              </Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}

function ImportedList({ title, items }: { title: string; items: string[] }) {
  const common = useTranslations("agentSkills.common");
  return (
    <div className="min-w-0">
      <p className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
        {title} (<span className="font-mono">{items.length}</span>)
      </p>
      {items.length === 0 ? (
        <p className="text-muted-foreground text-xs italic">{common("none")}</p>
      ) : (
        <ul className="flex max-h-48 min-w-0 flex-col gap-0.5 overflow-y-auto">
          {items.map((s) => (
            <li key={s} className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
              {s}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
