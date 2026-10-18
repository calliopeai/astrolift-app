"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useTranslations } from "next-intl";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";

export interface QuietHoursSettings {
  enabled: boolean;
  timezone: string;
  startMinute: number;
  endMinute: number;
  active: boolean;
}

export interface NotificationQuietHoursPanelProps {
  preferences: QuietHoursSettings | null;
  loading: boolean;
  error: boolean;
  saving: boolean;
  saveError: boolean;
  saved: boolean;
  onRetry: () => void;
  onSave: (input: Omit<QuietHoursSettings, "active">) => Promise<void>;
}

const clock = (minute: number) =>
  `${Math.floor(minute / 60)
    .toString()
    .padStart(2, "0")}:${(minute % 60).toString().padStart(2, "0")}`;
const minutes = (value: string) => {
  const [hour, minute] = value.split(":").map(Number);
  return hour * 60 + minute;
};

export function NotificationQuietHoursPanel(props: NotificationQuietHoursPanelProps) {
  const t = useTranslations("notificationSettings.quietHours");
  const time = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, t("invalidTime"));
  const schema = z
    .object({
      enabled: z.boolean(),
      timezone: z.string().trim().min(1, t("timezoneRequired")).max(64),
      start: time,
      end: time,
    })
    .refine((value) => value.start !== value.end, { path: ["end"], message: t("differentTimes") });
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    values: {
      enabled: props.preferences?.enabled ?? false,
      timezone: props.preferences?.timezone ?? "UTC",
      start: clock(props.preferences?.startMinute ?? 1320),
      end: clock(props.preferences?.endMinute ?? 480),
    },
  });
  const enabled = useWatch({ control: form.control, name: "enabled" });
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("title")}</CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {props.loading ? (
          <div className="space-y-3" aria-label={t("loading")}>
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : props.error || !props.preferences ? (
          <div role="alert" className="space-y-3">
            <p>{t("error")}</p>
            <Button variant="outline" onClick={props.onRetry}>
              {t("retry")}
            </Button>
          </div>
        ) : (
          <form
            className="max-w-xl space-y-4"
            onSubmit={form.handleSubmit(async (value) => {
              await props.onSave({
                enabled: value.enabled,
                timezone: value.timezone,
                startMinute: minutes(value.start),
                endMinute: minutes(value.end),
              });
            })}
          >
            <div className="flex items-center gap-3">
              <Checkbox
                id="quiet-hours-enabled"
                checked={enabled}
                onCheckedChange={(value) =>
                  form.setValue("enabled", value === true, { shouldDirty: true })
                }
                disabled={props.saving}
              />
              <Label htmlFor="quiet-hours-enabled">{t("enabled")}</Label>
            </div>
            <div className="space-y-2">
              <Label htmlFor="quiet-hours-timezone">{t("timezone")}</Label>
              <Input
                id="quiet-hours-timezone"
                {...form.register("timezone")}
                disabled={props.saving}
                autoComplete="off"
              />
              {form.formState.errors.timezone && (
                <p role="alert">{form.formState.errors.timezone.message}</p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="quiet-hours-start">{t("start")}</Label>
                <Input
                  id="quiet-hours-start"
                  type="time"
                  {...form.register("start")}
                  disabled={props.saving}
                />
                {form.formState.errors.start && (
                  <p role="alert">{form.formState.errors.start.message}</p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="quiet-hours-end">{t("end")}</Label>
                <Input
                  id="quiet-hours-end"
                  type="time"
                  {...form.register("end")}
                  disabled={props.saving}
                />
                {form.formState.errors.end && (
                  <p role="alert">{form.formState.errors.end.message}</p>
                )}
              </div>
            </div>
            <p className="text-muted-foreground text-sm" role="status">
              {t(props.preferences.active ? "active" : "inactive")}
            </p>
            {props.saveError && (
              <p role="alert" className="text-destructive text-sm">
                {t("saveError")}
              </p>
            )}
            {props.saved && (
              <p role="status" className="text-sm">
                {t("saved")}
              </p>
            )}
            <Button type="submit" disabled={props.saving}>
              {t("save")}
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
