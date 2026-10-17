"use client";

import { ArchiveIcon, ExternalLinkIcon, Loader2Icon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  DetailTabSections,
  type DetailTabSectionLink,
} from "@/components/detail/DetailTabSections";
import { Panel } from "@/components/panel/Panel";
import { Restricted } from "@/components/settings/Restricted";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DefinitionList } from "@/components/ui/definition-list";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { RebuildBrainMutation } from "@/graphql/__generated__/operations";

import { APPROVALS_HREF } from "../gated-write";
import type { GateAsk } from "./brain-detail-model";

export type BrainRebuild = NonNullable<RebuildBrainMutation["rebuildBrain"]["data"]>;

export interface BrainSettingsTabProps {
  layerName: string;
  section: "rebuild" | "danger-zone";
  sections: DetailTabSectionLink[];
  canRebuild: boolean;
  canArchive: boolean;
  rebuild: {
    environments: string[];
    environment: string;
    onEnvironment: (name: string) => void;
    ingest: boolean;
    onIngest: (on: boolean) => void;
    submitting: boolean;
    onRebuild: () => void;
    /** What the last rebuild started. */
    result: BrainRebuild | null;
    error: { message: string; approval: boolean } | null;
  };
  archive: {
    /** What brain.delete asks here: confirmation, a fresh sign-in, approval. */
    ask: GateAsk;
    submitting: boolean;
    /** Resolves true once archived. */
    onArchive: () => Promise<boolean>;
    error: { message: string; approval: boolean } | null;
  };
}

/**
 * Settings (#2424, #2427): Rebuild redeploys the deploy branch head, or with
 * ingest runs the layer's knowledge-refresh workflow first (its commit then
 * deploys through the normal push path); a production deploy asks for a
 * fresh sign-in. The danger zone archives the layer: scaled to zero, out of
 * the register, hosted apps' tokens revoked, its data and repository kept,
 * and restoreApp brings it back. Pure.
 */
export function BrainSettingsTab(props: BrainSettingsTabProps) {
  const t = useTranslations("brains.settings");
  return (
    <DetailTabSections ariaLabel={t("sections")} sections={props.sections} active={props.section}>
      {props.section === "danger-zone" ? <DangerZone {...props} /> : <Rebuild {...props} />}
    </DetailTabSections>
  );
}

function Refusal({ error }: { error: { message: string; approval: boolean } | null }) {
  const t = useTranslations("brains.settings");
  if (!error) return null;
  return (
    <p
      role="alert"
      className="border-danger-border bg-danger/5 text-danger-fg rounded-md border px-3 py-2 text-xs [overflow-wrap:anywhere]"
    >
      <span className="font-mono">{error.message}</span>
      {error.approval && (
        <>
          {" "}
          <Link href={APPROVALS_HREF} className="text-primary font-sans hover:underline">
            {t("viewRequests")}
          </Link>
        </>
      )}
    </p>
  );
}

function Rebuild({ canRebuild, rebuild }: BrainSettingsTabProps) {
  const t = useTranslations("brains.settings");
  const result = rebuild.result;
  const runUrl = result?.ingestRunUrl || result?.runUrl || null;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Restricted allowed={canRebuild} permission="app.deploy" verb={t("rebuilding")}>
        <Panel
          title={t("rebuildTitle")}
          icon={<RefreshCwIcon className="size-4" />}
          description={t("rebuildDescription")}
        >
          <div className="flex min-w-0 flex-col gap-4">
            <div className="grid min-w-0 gap-4 sm:grid-cols-[14rem_1fr] sm:items-start">
              <div className="min-w-0 space-y-2">
                <Label htmlFor="rebuild-environment">{t("environment")}</Label>
                <Select value={rebuild.environment} onValueChange={rebuild.onEnvironment}>
                  <SelectTrigger id="rebuild-environment" className="w-full font-mono">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {rebuild.environments.map((env) => (
                      <SelectItem key={env} value={env}>
                        <span className="font-mono">{env}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex min-w-0 items-start gap-2 sm:pt-7">
                <Checkbox
                  id="rebuild-ingest"
                  checked={rebuild.ingest}
                  onCheckedChange={(on) => rebuild.onIngest(on === true)}
                  aria-describedby="rebuild-ingest-note"
                />
                <div className="min-w-0">
                  <Label htmlFor="rebuild-ingest" className="font-normal">
                    {t("ingest")}
                  </Label>
                  <p id="rebuild-ingest-note" className="text-muted-foreground text-xs">
                    {t("ingestHelp")}
                  </p>
                </div>
              </div>
            </div>
            {["production", "prod"].includes(rebuild.environment) && (
              <p className="text-muted-foreground text-xs">{t("productionStepUp")}</p>
            )}
            <div>
              <Button onClick={rebuild.onRebuild} disabled={rebuild.submitting}>
                {rebuild.submitting ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <RefreshCwIcon className="size-4" />
                )}
                {rebuild.ingest ? t("rebuildWithIngest") : t("rebuild")}
              </Button>
            </div>
            <Refusal error={rebuild.error} />
          </div>
        </Panel>
      </Restricted>

      {result && (
        <Panel title={t("started")} span={12}>
          <DefinitionList
            items={[
              {
                term: t("mode"),
                description: (
                  <span className="font-mono">
                    {["deploy", "ci", "ingest"].includes(result.mode)
                      ? t(`modes.${result.mode}`)
                      : result.mode}
                  </span>
                ),
              },
              ...(result.deploymentId
                ? [
                    {
                      term: t("deployment"),
                      description: (
                        <Link
                          href={`/deployments/${result.deploymentId}`}
                          className="text-primary font-mono hover:underline"
                        >
                          {result.deploymentId.slice(0, 8)}
                        </Link>
                      ),
                    },
                  ]
                : []),
              ...(runUrl
                ? [
                    {
                      term: t("run"),
                      description: (
                        <a
                          href={runUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary inline-flex items-center gap-0.5 hover:underline"
                        >
                          {t("openRun")}
                          <ExternalLinkIcon className="size-3" aria-hidden />
                        </a>
                      ),
                    },
                  ]
                : []),
              ...(result.rebuilds.length
                ? [
                    {
                      term: t("rebuilds"),
                      description: (
                        <ul className="text-muted-foreground space-y-0.5 font-mono text-xs [overflow-wrap:anywhere]">
                          {result.rebuilds.map((r) => (
                            <li key={r}>{r}</li>
                          ))}
                        </ul>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </Panel>
      )}
    </div>
  );
}

function DangerZone({ layerName, canArchive, archive }: BrainSettingsTabProps) {
  const t = useTranslations("brains.settings");
  const [confirming, setConfirming] = React.useState(false);
  const asks = [
    archive.ask.verification ? t("askVerification") : null,
    archive.ask.approval ? t("askApproval", { count: archive.ask.minimumApprovals }) : null,
  ].filter(Boolean);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Restricted allowed={canArchive} permission="app.delete" verb={t("archiving")}>
        <div className="border-danger-border min-w-0 rounded-md border">
          <div className="flex min-w-0 flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <p className="text-sm font-medium">{t("archiveTitle")}</p>
              <p className="text-muted-foreground mt-1 text-sm">{t("archiveDescription")}</p>
              {asks.length > 0 && (
                <p className="text-muted-foreground mt-2 text-xs">{asks.join(" ")}</p>
              )}
            </div>
            <Button
              variant="destructive"
              size="sm"
              className="shrink-0"
              disabled={archive.submitting}
              onClick={() => setConfirming(true)}
            >
              {archive.submitting ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : (
                <ArchiveIcon className="size-3.5" />
              )}
              {t("archive")}
            </Button>
          </div>
        </div>
      </Restricted>
      <Refusal error={archive.error} />
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        destructive
        title={t("archiveConfirmTitle", { name: layerName })}
        description={[t("archiveDescription"), ...asks].join(" ")}
        confirmLabel={t("archive")}
        onConfirm={() => {
          // Closes at once: a fresh sign-in opens its own prompt, and a
          // refusal or an approval request is said on the page.
          void archive.onArchive();
        }}
      />
    </div>
  );
}
