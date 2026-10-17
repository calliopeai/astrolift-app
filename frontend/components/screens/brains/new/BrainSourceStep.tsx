"use client";

import { AlertTriangleIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

import {
  type DraftField,
  type NewBrainDraft,
  type Problem,
  visibilitiesFor,
} from "./new-brain-model";
import { WizardField } from "./WizardField";

export interface WizardConnection {
  id: string;
  name: string;
  kind: string;
  /** The account it acts as, when the host reports one. */
  accountLogin?: string | null;
}

export interface BrainSourceStepProps {
  draft: NewBrainDraft;
  onChange: (patch: Partial<NewBrainDraft>) => void;
  /** Connections that can act for the viewer. */
  connections: WizardConnection[];
  connectionsLoading: boolean;
  connectionsError: string | null;
  onRetryConnections: () => void;
  problems: Partial<Record<DraftField, Problem>>;
  serverErrors: Partial<Record<DraftField, string>>;
}

/** Where to connect a source account when none can create the repository. */
export const SOURCE_PROVIDERS_HREF = "/providers#source";

/**
 * Step 1 (#2427): the source connection that creates or fills the
 * repository, the repository itself (`owner/name`, missing or empty), its
 * visibility, and the Mimwell edition to scaffold from. Pure.
 */
export function BrainSourceStep({
  draft,
  onChange,
  connections,
  connectionsLoading,
  connectionsError,
  onRetryConnections,
  problems,
  serverErrors,
}: BrainSourceStepProps) {
  const t = useTranslations("brains.wizard");
  const connection = connections.find((c) => c.id === draft.connectionId) ?? null;
  const visibilities = visibilitiesFor(connection?.kind ?? "");
  // A missing value is marked by the asterisk and holds Continue; only a
  // value in the wrong shape is worth words.
  const shown = (field: DraftField) => {
    const problem = problems[field];
    return problem && problem !== "required" ? t(`problems.${problem}`) : null;
  };
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <WizardField
        id="brain-connection"
        label={t("connection")}
        required
        help={t("connectionHelp")}
        serverError={serverErrors.connectionId}
      >
        {connectionsLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : connectionsError ? (
          <div role="alert" className="flex flex-wrap items-center gap-2 text-sm">
            <AlertTriangleIcon className="text-danger size-4" aria-hidden />
            <span className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
              {connectionsError}
            </span>
            <Button type="button" size="sm" variant="outline" onClick={onRetryConnections}>
              {t("retry")}
            </Button>
          </div>
        ) : connections.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            {t.rich("noConnections", {
              link: (chunks) => (
                <Link href={SOURCE_PROVIDERS_HREF} className="text-primary hover:underline">
                  {chunks}
                </Link>
              ),
            })}
          </p>
        ) : (
          <Select
            value={draft.connectionId}
            onValueChange={(id) => {
              const next = connections.find((c) => c.id === id);
              const allowed = visibilitiesFor(next?.kind ?? "");
              onChange({
                connectionId: id,
                visibility: allowed.includes(draft.visibility) ? draft.visibility : "PRIVATE",
              });
            }}
          >
            <SelectTrigger id="brain-connection" className="w-full">
              <SelectValue placeholder={t("chooseConnection")} />
            </SelectTrigger>
            <SelectContent>
              {connections.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  <span className="font-medium">{c.name}</span>{" "}
                  <span className="text-muted-foreground font-mono text-xs">
                    {c.accountLogin ? `${c.accountLogin} · ${c.kind}` : c.kind}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </WizardField>
      {connection?.kind === "github_app_install" && (
        <p className="bg-warning/10 border-warning-border text-warning-fg rounded-md border px-3 py-2 text-xs">
          {t("appInstallNote")}
        </p>
      )}

      <div className="grid min-w-0 gap-4 sm:grid-cols-[1fr_12rem]">
        <WizardField
          id="brain-repo"
          label={t("repo")}
          required
          help={t("repoHelp")}
          problem={shown("repo")}
          serverError={serverErrors.repo}
        >
          <Input
            id="brain-repo"
            className="font-mono"
            placeholder="acme/platform-brain"
            value={draft.repo}
            onChange={(e) => onChange({ repo: e.target.value })}
            aria-invalid={Boolean(shown("repo") || serverErrors.repo) || undefined}
            aria-describedby="brain-repo-note"
            autoComplete="off"
            spellCheck={false}
          />
        </WizardField>
        <WizardField
          id="brain-visibility"
          label={t("visibility")}
          help={t("visibilityHelp")}
          serverError={serverErrors.visibility}
        >
          <Select
            value={draft.visibility}
            onValueChange={(v) => onChange({ visibility: v as NewBrainDraft["visibility"] })}
          >
            <SelectTrigger id="brain-visibility" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {visibilities.map((v) => (
                <SelectItem key={v} value={v}>
                  {t(`visibilities.${v}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </WizardField>
      </div>

      <WizardField
        id="brain-template"
        label={t("templateRef")}
        help={t("templateRefHelp")}
        problem={shown("templateRef")}
        serverError={serverErrors.templateRef}
      >
        <Input
          id="brain-template"
          className="font-mono"
          placeholder="v1.4.0"
          value={draft.templateRef}
          onChange={(e) => onChange({ templateRef: e.target.value })}
          aria-invalid={Boolean(shown("templateRef") || serverErrors.templateRef) || undefined}
          aria-describedby="brain-template-note"
          autoComplete="off"
          spellCheck={false}
        />
      </WizardField>
    </div>
  );
}
