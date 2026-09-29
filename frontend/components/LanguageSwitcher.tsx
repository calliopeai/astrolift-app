"use client";

import { GlobeIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { type Locale, locales } from "@/i18n/config";

/**
 * The language picker on Settings › Profile. Pure (Storybook first): the
 * locale and the switch come from useLocaleSwitch. Its sidebar variant went
 * with the old account menu; a settings page takes a select.
 */
export function LanguageSwitcher({
  locale,
  pending,
  onChange,
}: {
  locale: Locale;
  pending: boolean;
  onChange: (next: Locale) => void;
}) {
  const t = useTranslations("language");
  return (
    <Select value={locale} onValueChange={(v) => onChange(v as Locale)} disabled={pending}>
      <SelectTrigger className="w-48" aria-label={t("label")}>
        <GlobeIcon className="size-4" />
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {locales.map((l) => (
          <SelectItem key={l} value={l}>
            {t(l)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
