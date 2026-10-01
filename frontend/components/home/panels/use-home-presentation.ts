"use client";

import { useFormatter, useNow, useTranslations } from "next-intl";

export function useHomePresentation(liveClock = false) {
  const t = useTranslations("home");
  const fmt = useFormatter();
  const now = useNow(liveClock ? { updateInterval: 60_000 } : undefined);
  const number = (value: number) => fmt.number(value);
  const money = (cents: number, currency: string) =>
    fmt.number(cents / 100, {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    });
  const percent = (value: number) =>
    fmt.number(value, {
      style: "percent",
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
  const status = (value: string) => (t.has(`status.${value}`) ? t(`status.${value}`) : value);
  const age = (value: string) =>
    Number.isFinite(Date.parse(value))
      ? fmt.relativeTime(new Date(value), now)
      : t("copy.unknownTime");
  const duration = (seconds: number | null | undefined) => {
    if (seconds == null || !Number.isFinite(seconds)) return null;
    const rounded = Math.round(seconds);
    const m = Math.floor(rounded / 60),
      s = rounded % 60;
    const unit = (value: number, name: "minute" | "second") =>
      fmt.number(value, { style: "unit", unit: name, unitDisplay: "short" });
    return m === 0
      ? unit(s, "second")
      : s === 0
        ? unit(m, "minute")
        : `${unit(m, "minute")} ${unit(s, "second")}`;
  };
  const signal = (value: number, unit: string) => {
    if (!Number.isFinite(value)) return t("copy.unknownValue");
    switch (unit) {
      case "rps":
        return t("operations.requestRate", {
          value: fmt.number(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
        });
      case "ratio":
        return fmt.number(value, {
          style: "percent",
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        });
      case "percent":
        return percent(value / 100);
      case "seconds":
        return value < 1
          ? t("operations.milliseconds", { value: number(Math.round(value * 1000)) })
          : fmt.number(value, {
              style: "unit",
              unit: "second",
              unitDisplay: "short",
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            });
      default:
        return fmt.number(value, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
    }
  };
  return { t, fmt, number, money, percent, status, age, duration, signal };
}
