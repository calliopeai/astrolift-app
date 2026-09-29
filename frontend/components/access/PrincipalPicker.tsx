"use client";

import { SearchIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import type { Principal } from "./access-model";
import type { PrincipalSearch } from "./GrantAccessFlow";
import { PrincipalChip } from "./PrincipalChip";

export interface PrincipalPickerProps {
  /** Names the field: "Who", "Compare with". */
  label: string;
  value: Principal | null;
  onChange: (principal: Principal | null) => void;
  search: PrincipalSearch;
  /** One-click picks above the search, such as the viewer as "Me". */
  quickPicks?: { label: string; principal: Principal }[];
  placeholder?: string;
  className?: string;
}

const keyOf = (p: Principal) => `${p.kind}:${p.id}`;

/**
 * One principal, picked (design 3.7): the pick as a `PrincipalChip` with
 * Change, or a search box with its results in their own scroll frame and any
 * quick picks above it. The search is the hook's (debounced there). Pure.
 */
export function PrincipalPicker({
  label,
  value,
  onChange,
  search,
  quickPicks = [],
  placeholder = "Search people by name or email",
  className,
}: PrincipalPickerProps) {
  const id = React.useId();
  const results = search.results.filter((p) => !value || keyOf(p) !== keyOf(value));

  return (
    <div role="group" aria-labelledby={id} className={cn("flex min-w-0 flex-col gap-2", className)}>
      <span id={id} className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        {label}
      </span>

      {value ? (
        <div className="flex min-w-0 items-center gap-2 rounded-md border px-3 py-2">
          <PrincipalChip principal={value} variant="block" className="flex-1" />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => onChange(null)}
            aria-label={`Change ${label.toLowerCase()}`}
          >
            Change
          </Button>
        </div>
      ) : (
        <>
          {quickPicks.length > 0 && (
            <div className="flex min-w-0 flex-wrap gap-2">
              {quickPicks.map((q) => (
                <Button
                  key={keyOf(q.principal)}
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => onChange(q.principal)}
                  className="max-w-full min-w-0"
                >
                  <span className="truncate">{q.label}</span>
                </Button>
              ))}
            </div>
          )}
          <div className="relative min-w-0">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input
              type="search"
              value={search.query}
              onChange={(e) => search.setQuery(e.target.value)}
              placeholder={placeholder}
              aria-label={`${label}: ${placeholder}`}
              className="pl-8"
            />
          </div>
          <div className="max-h-56 min-w-0 overflow-auto rounded-md border">
            {search.error ? (
              <p role="alert" className="text-danger-fg p-3 text-sm [overflow-wrap:anywhere]">
                Search failed: {search.error.message}
              </p>
            ) : search.loading ? (
              <div className="flex flex-col gap-2 p-3" aria-busy>
                {Array.from({ length: 3 }, (_, i) => (
                  <Skeleton key={i} className="h-8" />
                ))}
              </div>
            ) : results.length === 0 ? (
              <p className="text-muted-foreground p-3 text-sm [overflow-wrap:anywhere]">
                {search.query.trim()
                  ? `No one matches "${search.query.trim()}".`
                  : "Type to search."}
              </p>
            ) : (
              <ul className="divide-y">
                {results.map((p) => (
                  <li key={keyOf(p)} className="min-w-0">
                    <button
                      type="button"
                      onClick={() => onChange(p)}
                      aria-label={`Pick ${p.name}`}
                      className="hover:bg-muted/60 focus-visible:ring-ring flex w-full min-w-0 items-center px-3 py-2 text-left focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
                    >
                      <PrincipalChip principal={{ ...p, href: undefined }} variant="block" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
