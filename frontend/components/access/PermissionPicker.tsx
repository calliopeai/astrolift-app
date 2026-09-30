"use client";

import { CheckIcon, SearchIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";

import { Input } from "@/components/ui/input";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";
import { cn } from "@/lib/utils";

import { localizedResourceLabel } from "./access-copy";
import { buildMatrix } from "./access-model";

export interface PermissionPickerProps {
  value: string | null;
  onChange: (permission: string) => void;
  /** The catalog; the generated one by default. */
  catalog?: readonly string[];
  label?: string;
  className?: string;
}

/**
 * One permission from the catalog (design 3.7), grouped by area the way the
 * permission matrix is (Agents, Apps, Workflows, Admin, Other), then by
 * resource. A filter narrows by slug; the list scrolls in its own frame.
 * Slugs are mono. Pure.
 */
export function PermissionPicker({
  value,
  onChange,
  catalog = ASTROLIFT_PERMISSIONS,
  label,
  className,
}: PermissionPickerProps) {
  const t = useTranslations("shared.access");
  const id = React.useId();
  const [filter, setFilter] = React.useState("");
  const areas = React.useMemo(() => buildMatrix(catalog), [catalog]);
  const needle = filter.trim().toLowerCase();

  const shown = areas
    .map((area) => ({
      ...area,
      rows: area.rows
        .map((row) => ({ ...row, all: row.all.filter((s) => s.toLowerCase().includes(needle)) }))
        .filter((row) => row.all.length > 0),
    }))
    .filter((area) => area.rows.length > 0);

  return (
    <div role="group" aria-labelledby={id} className={cn("flex min-w-0 flex-col gap-2", className)}>
      <span id={id} className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        {label ?? t("permissionPicker.label")}
      </span>
      <div className="relative min-w-0">
        <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
        <Input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t("permissionPicker.placeholder")}
          aria-label={t("matrix.filter")}
          className="pl-8 font-mono"
        />
      </div>
      <div className="max-h-64 min-w-0 overflow-auto rounded-md border">
        {shown.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm [overflow-wrap:anywhere]">
            {needle ? t("matrix.noMatch", { query: filter.trim() }) : t("permissionPicker.empty")}
          </p>
        ) : (
          shown.map((area) => (
            <section
              key={area.key}
              aria-label={t(`presentation.area.${area.key}`)}
              className="min-w-0 border-b last:border-b-0"
            >
              <h3 className="bg-muted/40 text-muted-foreground sticky top-0 px-3 py-1 text-xs font-medium">
                {t(`presentation.area.${area.key}`)}
              </h3>
              {area.rows.map((row) => (
                <div key={row.resource} className="min-w-0 px-3 py-1.5">
                  <p className="text-muted-foreground mb-1 text-xs">
                    {localizedResourceLabel(row.resource, t)}
                  </p>
                  <ul className="flex min-w-0 flex-wrap gap-1">
                    {row.all.map((slug) => {
                      const on = slug === value;
                      return (
                        <li key={slug} className="max-w-full min-w-0">
                          <button
                            type="button"
                            aria-pressed={on}
                            onClick={() => onChange(slug)}
                            className={cn(
                              "focus-visible:ring-ring inline-flex max-w-full min-w-0 items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-xs focus-visible:ring-2 focus-visible:outline-none",
                              on
                                ? "border-primary bg-primary/10 text-foreground"
                                : "hover:bg-muted/60 text-muted-foreground"
                            )}
                          >
                            {on && <CheckIcon aria-hidden className="size-3 shrink-0" />}
                            <span className="min-w-0 truncate" title={slug}>
                              {slug}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </section>
          ))
        )}
      </div>
    </div>
  );
}
