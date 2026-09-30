"use client";

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
  if (access !== "ready") {
    return (
      <p role="status" className="text-muted-foreground text-sm">
        {access === "loading"
          ? "Checking access to environment specs…"
          : access === "denied"
            ? "Reading environment specs requires agent_env_spec.read."
            : "Access to environment specs could not be checked. Reload this page to try again."}
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
          <h2 className="text-sm font-medium">Environment spec secrets</h2>
          <p className="text-muted-foreground text-sm">
            Choose a visible environment spec to manage its secrets. Edits affect every run or box
            using this recipe. Select the recipe separately when dispatching an agent.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="secret-source-search">Search environment specs</Label>
          <Input
            id="secret-source-search"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="Search names, slugs, runtimes, repos…"
          />
          <Label htmlFor="secret-source">Environment spec</Label>
          <Select
            value={selection?.id ?? ""}
            onValueChange={onSelect}
            disabled={optionsLoading || Boolean(optionsError)}
          >
            <SelectTrigger id="secret-source" className="w-full min-w-0">
              <SelectValue
                placeholder={optionsLoading ? "Loading specs…" : "Choose an environment spec"}
              />
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
            <p className="text-destructive text-sm">Environment specs could not be loaded.</p>
            <Button variant="outline" size="sm" onClick={onRetryOptions}>
              Retry choices
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground" role="status">
              {optionsLoading
                ? "Loading choices…"
                : `${totalCount} visible spec${totalCount === 1 ? "" : "s"} · Page ${page}`}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={optionsLoading || page <= 1}
              onClick={() => onPage(page - 1)}
            >
              Previous specs
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={optionsLoading || page * pageSize >= totalCount}
              onClick={() => onPage(page + 1)}
            >
              Next specs
            </Button>
          </div>
        )}
        {!optionsLoading && !optionsError && totalCount === 0 && (
          <p role="status" className="text-muted-foreground text-sm">
            No visible environment specs match this search.
          </p>
        )}
        {selection && (
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={onClear}>
              Clear source
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={sourceState === "loading"}
              onClick={onVerify}
            >
              Verify source again
            </Button>
          </div>
        )}
      </section>
      {sourceState === "unselected" && (
        <p role="status" className="text-muted-foreground text-sm">
          Select an environment spec to view its values and bundles.
        </p>
      )}
      {sourceState === "loading" && (
        <p role="status" className="text-muted-foreground text-sm">
          Checking the selected spec…
        </p>
      )}
      {sourceState === "unavailable" && (
        <p role="status" className="text-muted-foreground text-sm">
          The selected spec is no longer available. Choose a visible spec.
        </p>
      )}
      {sourceState === "error" && (
        <p role="alert" className="text-destructive text-sm">
          The selected spec could not be verified. Try verifying the source again.
        </p>
      )}
      {sourceState === "confirmed" && !canListSecrets && (
        <p role="status" className="text-muted-foreground text-sm">
          Listing this spec&apos;s secret values and bundles requires secret.list.
        </p>
      )}
      {sourceState === "confirmed" && canListSecrets && children}
    </div>
  );
}

/** Known missing bundle-read permission never starts attachment queries. */
export function AgentSecretBundleReadDenied() {
  return (
    <p className="text-muted-foreground text-sm" role="status">
      Reading this spec&apos;s bundle attachments requires secret.read and secret.list.
    </p>
  );
}
