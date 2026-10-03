"use client";

import { useEffect, useId, useRef } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { SearchIcon } from "lucide-react";
import { ListPage } from "@/components/list/ListPage";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CatalogueState, HuggingFaceModel } from "@/graphql/__generated__/schema";
import type { ModelPage } from "./ModelSubscriptionsPanel";

export interface HuggingFaceCataloguePanelProps {
  page: ModelPage<HuggingFaceModel>;
  hostingAllowed: boolean | null;
  state: CatalogueState | null;
  source: string | null;
  observedAt: string | null;
  retryAfterSeconds: number | null;
  selectedRepoId: string | null;
  revision: string;
  onSelect: (repoId: string) => void;
  onRevisionChange: (revision: string) => void;
  resolving: boolean;
  resolvedModel: HuggingFaceModel | null;
  resolutionError: string | null;
  resolutionSource: string | null;
  resolutionObservedAt: string | null;
  onRetryResolution: () => void;
  onUseRevision: (model: { repoId: string; revisionSha: string }) => void;
}

export function HuggingFaceCataloguePanel(props: HuggingFaceCataloguePanelProps) {
  const {
    page,
    state,
    source,
    observedAt,
    retryAfterSeconds,
    selectedRepoId,
    revision,
    onSelect,
    onRevisionChange,
    resolving,
    resolvedModel,
    resolutionError,
    resolutionSource,
    resolutionObservedAt,
    onRetryResolution,
    onUseRevision,
  } = props;
  const t = useTranslations("models.shared.catalogue");
  const hosting = useTranslations("models.shared.hosting");
  const fmt = useFormatter();
  const id = useId();
  const selection = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selectedRepoId) {
      selection.current?.scrollIntoView?.({
        block: "start",
        behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
      });
    }
  }, [selectedRepoId]);
  const revisionSha = resolvedModel?.revisionSha;
  const pinned =
    !resolving &&
    !resolutionError &&
    resolvedModel &&
    resolvedModel.repoId === selectedRepoId &&
    typeof revisionSha === "string" &&
    /^[a-f0-9]{40}$/i.test(revisionSha)
      ? { repoId: resolvedModel.repoId, revisionSha }
      : null;
  const formatTime = (value: string | null) => {
    const date = value ? new Date(value) : null;
    return date && Number.isFinite(date.getTime())
      ? fmt.dateTime(date, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }) + " UTC"
      : t("unknown");
  };
  const available = state === "AVAILABLE" && !page.loading && !page.stale && !page.error;
  const modelFact = (model: HuggingFaceModel) => (
    <div className="space-y-1">
      <p>{model.pipelineTag ?? t("unknown")}</p>
      <p className="text-muted-foreground text-xs">
        {model.library ?? t("unknown")} · {model.license ?? t("unknown")}
      </p>
      {model.architectures.length > 0 && (
        <p className="text-muted-foreground text-xs break-all">{model.architectures.join(", ")}</p>
      )}
    </div>
  );
  return (
    <section className="space-y-4" aria-labelledby={`${id}-title`}>
      <div className="space-y-2">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {t("title")}
        </h2>
        <p className="text-muted-foreground text-sm">{t("description")}</p>
        <details className="text-sm">
          <summary className="text-muted-foreground cursor-pointer">
            {hosting("reviewStep")}
          </summary>
          <dl className="mt-3 grid gap-3 text-sm @lg:grid-cols-2">
            <div>
              <dt className="font-medium">{hosting("accessTitle")}</dt>
              <dd>{hosting("accessUnknown")}</dd>
            </div>
            <div>
              <dt className="font-medium">{hosting("licenseTitle")}</dt>
              <dd>{hosting("licenseHelp")}</dd>
            </div>
            <div>
              <dt className="font-medium">{hosting("runtimeTitle")}</dt>
              <dd>{hosting("runtimePending")}</dd>
            </div>
            <div>
              <dt className="font-medium">{hosting("hardwareTitle")}</dt>
              <dd>{hosting("hardwareHelp")}</dd>
            </div>
          </dl>
        </details>
      </div>
      {source && (
        <p className="text-muted-foreground text-xs break-all">
          {t("source", {
            source,
            time: formatTime(observedAt),
          })}
        </p>
      )}
      {state === "RATE_LIMITED" && retryAfterSeconds !== null && (
        <p role="status">{t("retryAfter", { seconds: retryAfterSeconds })}</p>
      )}
      {props.hostingAllowed !== true && (
        <p role="status">
          {hosting(props.hostingAllowed === null ? "adminChecking" : "adminRequired")}
        </p>
      )}
      <ListPage
        embedded
        {...page}
        error={
          page.error ??
          (state === "RATE_LIMITED" || state === "UNAVAILABLE"
            ? { message: t(state === "RATE_LIMITED" ? "rateLimited" : "unavailable") }
            : null)
        }
        label={t("title")}
        getRowId={(model) => model.repoId}
        empty={{ icon: <SearchIcon />, title: t("empty"), description: t("emptyDescription") }}
        columns={[
          {
            id: "host",
            header: hosting("host"),
            cell: (model) => (
              <Button
                type="button"
                variant="outline"
                disabled={!available || props.hostingAllowed !== true}
                onClick={() => onSelect(model.repoId)}
              >
                {hosting("host")}
              </Button>
            ),
          },
          {
            id: "repoId",
            header: t("repository"),
            cellClassName: "max-w-72",
            cell: (model) => (
              <div>
                <Button
                  type="button"
                  variant="ghost"
                  className="h-auto max-w-full text-left break-all whitespace-normal"
                  disabled={!available}
                  onClick={() => onSelect(model.repoId)}
                >
                  {model.repoId}
                </Button>
                <p className="text-muted-foreground text-xs break-all">
                  {model.author ?? t("unknown")}
                </p>
              </div>
            ),
          },
          { id: "metadata", header: t("metadata"), cellClassName: "max-w-64", cell: modelFact },
          { id: "gated", header: t("gating"), cell: (model) => t(`gated.${model.gated}`) },
          {
            id: "popularity",
            header: t("popularity"),
            cell: (model) => (
              <div className="space-y-1 text-xs">
                <p>
                  {t("downloads", {
                    value:
                      model.downloads === null || model.downloads === undefined
                        ? t("unknown")
                        : fmt.number(model.downloads),
                  })}
                </p>
                <p>
                  {t("likes", {
                    value:
                      model.likes === null || model.likes === undefined
                        ? t("unknown")
                        : fmt.number(model.likes),
                  })}
                </p>
              </div>
            ),
          },
        ]}
      />
      {selectedRepoId && (
        <div ref={selection} className="bg-surface-1 scroll-mt-6 space-y-3 rounded-md border p-4">
          <h3 className="font-medium break-all">{selectedRepoId}</h3>
          <Label htmlFor={`${id}-revision`}>{t("revision")}</Label>
          <Input
            id={`${id}-revision`}
            value={revision}
            onChange={(event) => onRevisionChange(event.target.value)}
            maxLength={128}
            aria-describedby={`${id}-revision-help`}
          />
          <p id={`${id}-revision-help`} className="text-muted-foreground text-sm">
            {t("revisionHelp")}
          </p>
          {resolving && <p role="status">{t("resolving")}</p>}
          {resolutionError && (
            <div role="alert" className="space-y-2">
              <p>{resolutionError}</p>
              <Button variant="outline" onClick={onRetryResolution}>
                {t("retry")}
              </Button>
            </div>
          )}
          {resolutionSource && (
            <p className="text-muted-foreground text-xs break-all">
              {t("source", { source: resolutionSource, time: formatTime(resolutionObservedAt) })}
            </p>
          )}
          {pinned ? (
            <p className="font-mono text-xs break-all">{pinned.revisionSha}</p>
          ) : (
            !resolving && !resolutionError && <p role="status">{t("noVerifiedRevision")}</p>
          )}
          <Button
            type="button"
            disabled={!pinned || props.hostingAllowed !== true}
            onClick={() => {
              if (pinned && props.hostingAllowed === true) onUseRevision(pinned);
            }}
          >
            {hosting("continue")}
          </Button>
        </div>
      )}
    </section>
  );
}
