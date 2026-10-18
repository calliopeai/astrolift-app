"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { TemporaryAppRules, TemporaryAppValues } from "./temporary-app-model";
import { temporaryAppValid } from "./temporary-app-model";

export interface TemporaryAppFieldsProps {
  value: TemporaryAppValues;
  onChange: (value: TemporaryAppValues) => void;
  rules: TemporaryAppRules | null;
  loading?: boolean;
  failed?: boolean;
  disabled?: boolean;
  inherited?: string;
  onRetry?: () => void;
}

export function TemporaryAppFields({
  value,
  onChange,
  rules,
  loading,
  failed,
  disabled,
  inherited = "",
  onRetry,
}: TemporaryAppFieldsProps) {
  const t = useTranslations("apps.temporaryCreation");
  const id = useId();
  if (loading)
    return (
      <p role="status" className="text-muted-foreground text-sm">
        {t("loading")}
      </p>
    );
  if (failed)
    return (
      <div className="space-y-2">
        <p role="alert" className="text-destructive text-sm">
          {t("unavailable")}
        </p>
        {onRetry && (
          <Button type="button" variant="outline" onClick={onRetry}>
            {t("retry")}
          </Button>
        )}
      </div>
    );
  if (!rules) return <p className="text-muted-foreground text-xs">{t("unsupported")}</p>;
  const effective = value.expiresIn?.trim() || inherited;
  const valid = temporaryAppValid(value, rules, inherited);
  return (
    <div className="space-y-3">
      <div className="space-y-2">
        <Label htmlFor={`${id}-ttl`}>{t("ttl")}</Label>
        <Input
          id={`${id}-ttl`}
          value={value.expiresIn ?? ""}
          onChange={(event) => onChange({ ...value, expiresIn: event.target.value })}
          placeholder={inherited || "30m, 4h, 7d, 6w, 3mo"}
          disabled={disabled}
          aria-invalid={!valid}
          aria-describedby={`${id}-help ${id}-policy`}
        />
        <p id={`${id}-help`} className="text-muted-foreground text-xs">
          {inherited ? t("inherited", { ttl: inherited }) : t("help")}
        </p>
        <p id={`${id}-policy`} className="text-muted-foreground text-xs">
          {t("bounds", {
            min: rules.policy.minTtlSeconds / 60,
            max: rules.policy.maxTtlSeconds / 60,
          })}
        </p>
        {!rules.permanentAllowed && <p className="text-warning-fg text-xs">{t("required")}</p>}
        {!valid && (
          <p role="alert" className="text-destructive text-xs">
            {t("invalid")}
          </p>
        )}
      </div>
      {effective && (
        <div className="space-y-2">
          <div className="flex items-start gap-2">
            <Checkbox
              id={`${id}-delete`}
              checked={value.deleteDataOnExpiry ?? false}
              disabled={disabled}
              onCheckedChange={(checked) =>
                onChange({ ...value, deleteDataOnExpiry: checked === true })
              }
            />
            <Label htmlFor={`${id}-delete`} className="text-sm leading-5">
              {t("deleteData")}
            </Label>
          </div>
          <p className="text-muted-foreground text-xs">
            {value.deleteDataOnExpiry
              ? t("deleteWarning")
              : t("keepData", { days: rules.policy.dataGraceDays })}
          </p>
        </div>
      )}
    </div>
  );
}
