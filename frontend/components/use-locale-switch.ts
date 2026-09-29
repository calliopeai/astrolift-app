"use client";

import { useLocale } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

import { setLocale } from "@/app/actions/locale";
import type { Locale } from "@/i18n/config";

/** The current locale and the switch to another: the data half of LanguageSwitcher. */
export function useLocaleSwitch() {
  const locale = useLocale() as Locale;
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return {
    locale,
    pending,
    onChange: (next: Locale) =>
      startTransition(async () => {
        await setLocale(next);
        router.refresh();
      }),
  };
}
