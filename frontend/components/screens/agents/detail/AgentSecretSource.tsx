"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ReactNode } from "react";

export interface SecretSourceOption {
  id: string;
  slug: string;
  name: string;
  teamId?: string | null;
  projectId?: string | null;
}

export interface AgentSecretSourceProps {
  access: "loading" | "denied" | "error" | "ready";
  options: SecretSourceOption[];
  optionsLoading: boolean;
  optionsError: string | null;
  search: string;
  onSearch: (value: string) => void;
  page: number;
  totalCount: number;
  pageSize: number;
  onPage: (page: number) => void;
  onRetryOptions: () => void;
  selection: SecretSourceOption | null;
  onSelect: (id: string) => void;
  onClear: () => void;
  sourceState: "unselected" | "loading" | "unavailable" | "error" | "confirmed";
  onVerify: () => void;
  canListSecrets: boolean;
  children?: ReactNode;
}

/** An explicit recipe choice; this page does not configure agent dispatch. */
export function AgentSecretSource({
  access,
  options,
  optionsLoading,
  optionsError,
  search,
  onSearch,
  page,
  totalCount,
  pageSize,
  onPage,
  onRetryOptions,
  selection,
  onSelect,
  onClear,
  sourceState,
  onVerify,
  canListSecrets,
  children,
}: AgentSecretSourceProps) {
  const t = useTranslations("agentSecrets.source");
  if (access !== "ready") {
    return (
      <p role="status" className="text-muted-foreground text-sm">
        {access === "loading"
          ? t("accessLoading")
          : access === "denied"
            ? t("accessDenied")
            : t("accessError")}
      </p>
    );
  }
  const choices =
    selection && !options.some((option) => option.id === selection.id)
      ? [selection, ...options]
      : options;
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <section className="flex min-w-0 flex-col gap-3 rounded-md border p-4">
        <div className="space-y-1">
          <h2 className="text-sm font-medium">{t("title")}</h2>
          <p className="text-muted-foreground text-sm">{t("description")}</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="secret-source-search">{t("search")}</Label>
          <Input
            id="secret-source-search"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder={t("searchPlaceholder")}
          />
          <Label htmlFor="secret-source">{t("spec")}</Label>
          <Select
            value={selection?.id ?? ""}
            onValueChange={onSelect}
            disabled={optionsLoading || Boolean(optionsError)}
          >
            <SelectTrigger id="secret-source" className="w-full min-w-0">
              <SelectValue placeholder={optionsLoading ? t("loadingSpecs") : t("chooseSpecs")} />
            </SelectTrigger>
            <SelectContent>
              {choices.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  <span className="truncate" title={`${option.name} (${option.slug})`}>
                    {option.name} ({option.slug})
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {optionsError ? (
          <div role="alert" className="space-y-2">
            <p className="text-destructive text-sm">{t("listError")}</p>
            <Button variant="outline" size="sm" onClick={onRetryOptions}>
              {t("retryChoices")}
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground" role="status">
              {optionsLoading ? t("loadingChoices") : t("count", { count: totalCount, page })}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={optionsLoading || page <= 1}
              onClick={() => onPage(page - 1)}
            >
              {t("previous")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={optionsLoading || page * pageSize >= totalCount}
              onClick={() => onPage(page + 1)}
            >
              {t("next")}
            </Button>
          </div>
        )}
        {!optionsLoading && !optionsError && totalCount === 0 && (
          <p role="status" className="text-muted-foreground text-sm">
            {t("empty")}
          </p>
        )}
        {selection && (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={onClear}>
              {t("clear")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={sourceState === "loading"}
              onClick={onVerify}
            >
              {t("verify")}
            </Button>
          </div>
        )}
      </section>
      {sourceState === "unselected" && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("unselected")}
        </p>
      )}
      {sourceState === "loading" && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("checking")}
        </p>
      )}
      {sourceState === "unavailable" && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("unavailable")}
        </p>
      )}
      {sourceState === "error" && (
        <p role="alert" className="text-destructive text-sm">
          {t("verifyError")}
        </p>
      )}
      {sourceState === "confirmed" && !canListSecrets && (
        <p role="status" className="text-muted-foreground text-sm">
          {t("listDenied")}
        </p>
      )}
      {sourceState === "confirmed" && canListSecrets && children}
    </div>
  );
}

/** Known missing bundle-read permission never starts attachment queries. */
export function AgentSecretBundleReadDenied() {
  const t = useTranslations("agentSecrets.source");
  return (
    <p className="text-muted-foreground text-sm" role="status">
      {t("bundlesDenied")}
    </p>
  );
}
