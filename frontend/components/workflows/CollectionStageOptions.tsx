"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export interface CollectionStageOptionsProps {
  kind: string;
  iteration: unknown;
  targets: string[];
  disabled: boolean;
  onChange: (value: unknown) => void;
}

/** Serial body ranges retain their source inputs while a bound is edited. */
export function CollectionStageOptions({
  kind,
  iteration,
  targets,
  disabled,
  onChange,
}: CollectionStageOptionsProps) {
  const t = useTranslations("workflowCollections");
  const id = useId();
  if (!["collection", "format_record"].includes(kind)) return null;
  const config =
    iteration && typeof iteration === "object" && !Array.isArray(iteration)
      ? (iteration as Record<string, unknown>)
      : {};
  const imported = config.source_format === "langflow_loop";
  const patch = (value: Record<string, unknown>) => onChange({ ...config, ...value });
  const items = Array.isArray(config.items) ? config.items : null;
  const invalid =
    kind === "collection" &&
    (!Number.isInteger(config.max_items) ||
      Number(config.max_items) < 1 ||
      Number(config.max_items) > 50 ||
      (items !== null && items.length > Number(config.max_items)) ||
      typeof config.body_end !== "string" ||
      !targets.includes(config.body_end));
  return (
    <fieldset disabled={disabled} className="flex min-w-0 flex-col gap-3 rounded-md border p-3">
      <legend className="px-1 text-xs font-medium">
        {t(kind === "collection" ? "title" : "formatKind")}
      </legend>
      {kind === "collection" ? (
        <>
          <p className="text-muted-foreground text-xs">{t("serialHint")}</p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-cap`} className="text-xs">
              {t("cap")}
            </Label>
            <Input
              id={`${id}-cap`}
              type="number"
              min={1}
              max={50}
              value={typeof config.max_items === "number" ? config.max_items : ""}
              className="h-8 text-xs"
              onChange={(event) => patch({ max_items: Number(event.target.value) })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-end`} className="text-xs">
              {t("bodyEnd")}
            </Label>
            <select
              id={`${id}-end`}
              className="border-input bg-background h-8 rounded-md border px-2 text-xs"
              value={typeof config.body_end === "string" ? config.body_end : ""}
              disabled={disabled || imported}
              onChange={(event) => patch({ body_end: event.target.value })}
            >
              {typeof config.body_end === "string" && !targets.includes(config.body_end) && (
                <option value={config.body_end}>{config.body_end}</option>
              )}
              {targets.map((target) => (
                <option key={target} value={target}>
                  {target}
                </option>
              ))}
            </select>
          </div>
          {items !== null ? (
            <p className="text-muted-foreground text-xs">
              {t("staticItems", { count: items.length })}
            </p>
          ) : (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${id}-path`} className="text-xs">
                {t("itemsPath")}
              </Label>
              <Input
                id={`${id}-path`}
                value={typeof config.items_path === "string" ? config.items_path : ""}
                className="h-8 text-xs"
                onChange={(event) => patch({ items_path: event.target.value })}
              />
            </div>
          )}
          {imported && <p className="text-muted-foreground text-xs">{t("importedHint")}</p>}
          {invalid && (
            <p role="alert" className="text-danger-fg text-xs">
              {t("invalid")}
            </p>
          )}
        </>
      ) : (
        <>
          <p className="text-muted-foreground text-xs">{t("formatHint")}</p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-pattern`} className="text-xs">
              {t("pattern")}
            </Label>
            <Input
              id={`${id}-pattern`}
              value={typeof config.pattern === "string" ? config.pattern : ""}
              className="h-8 text-xs"
              onChange={(event) => patch({ pattern: event.target.value })}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`${id}-separator`} className="text-xs">
              {t("separator")}
            </Label>
            <Input
              id={`${id}-separator`}
              value={
                typeof config.separator === "string" ? JSON.stringify(config.separator) : '"\\n"'
              }
              className="h-8 text-xs"
              readOnly
            />
          </div>
        </>
      )}
    </fieldset>
  );
}
