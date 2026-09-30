"use client";

import { useTranslations } from "next-intl";
import { useCallback } from "react";

import { handleVersionMismatch, type MutationEnvelopeLike } from "./version-mismatch";

type Options = Parameters<typeof handleVersionMismatch>[1];

export function useVersionMismatch() {
  const t = useTranslations("shared.versionMismatch");
  return useCallback(
    (envelope: MutationEnvelopeLike | null | undefined, options: Options = {}) =>
      handleVersionMismatch(envelope, {
        fallbackMessage: t("changed"),
        refreshLabel: t("refresh"),
        ...options,
      }),
    [t]
  );
}
