"use client";

import { SearchIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import type { LogLevel } from "@/components/observability/LogViewer";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { LOG_LEVEL_FILTERS, type LogLevelFilter } from "./app-log-lines";

export interface LogFiltersProps {
  level: LogLevelFilter;
  onLevelChange: (level: LogLevelFilter) => void;
  query: string;
  onQueryChange: (query: string) => void;
  /** Per-level counts over the whole buffer, shown in the picker. */
  counts?: Record<LogLevel, number>;
  /** Pickers before the filters: pod, container, time range. */
  children?: React.ReactNode;
}

/**
 * The filter row over an app log (spec 44 §5.5): the target pickers the
 * caller passes, then level and text. Wraps at 768px; nothing widens it.
 */
export function LogFilters({
  level,
  onLevelChange,
  query,
  onQueryChange,
  counts,
  children,
}: LogFiltersProps) {
  const t = useTranslations("apps.logViewer");
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      {children}
      <Select value={level} onValueChange={(v) => onLevelChange(v as LogLevelFilter)}>
        <SelectTrigger size="sm" aria-label={t("levelGroupLabel")} className="text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {LOG_LEVEL_FILTERS.map((l) => (
            <SelectItem key={l} value={l}>
              {t(`level.${l}`)}
              {counts && l !== "all" && (
                <span className="text-muted-foreground font-mono tabular-nums">· {counts[l]}</span>
              )}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="relative min-w-0 flex-1 sm:max-w-xs">
        <SearchIcon
          aria-hidden
          className="text-muted-foreground pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2"
        />
        <Input
          type="search"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={t("filterPlaceholder")}
          aria-label={t("searchAriaLabel")}
          className="h-8 pl-7 font-mono text-xs"
        />
      </div>
    </div>
  );
}
