"use client";

import { type LinkProps, type MessageOverrides, UIProvider } from "@calliopelabs/ui";
import NextLink from "next/link";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo } from "react";

/** next/link for every link a @calliopelabs/ui component draws. */
function Link({ href, ...props }: LinkProps) {
  return <NextLink href={href} {...props} />;
}

/**
 * The environment @calliopelabs/ui components read through `useUI()`:
 * client-side links and the app's own translations. Groups the app already
 * translates are mapped here; a group is added when the component that uses
 * it moves onto the library, so nothing renders in English by accident.
 * Mount inside NextIntlClientProvider.
 */
export function UiEnvironment({ children }: { children: ReactNode }) {
  const confirmation = useTranslations("shared.confirmation");
  const table = useTranslations("shared.table");
  const shellHeader = useTranslations("shared.shellHeader");
  const primitives = useTranslations("uiPrimitives");

  const messages = useMemo<MessageOverrides>(
    () => ({
      shell: { close: primitives("close") },
      dialog: { close: primitives("close") },
      breadcrumb: {
        label: shellHeader("breadcrumb"),
        switchFrom: (label) => shellHeader("switch", { label }),
      },
      confirm: {
        confirm: confirmation("confirm"),
        cancel: confirmation("cancel"),
        working: confirmation("working"),
        failed: confirmation("failed"),
        reasonRequired: confirmation("reasonRequired"),
      },
      table: {
        search: table("search"),
        clearSearch: table("clearSearch"),
        results: (count) => table("results", { count }),
        loading: table("loading"),
        loadFailed: (label) => table("loadFailed", { label }),
        requestFailed: table("requestFailed"),
        retry: table("retry"),
        noMatches: table("noMatches"),
        noMatchesDescription: (label) => table("noMatchesDescription", { label }),
        selected: (count) => table("selected", { count }),
        clearSelection: table("clear"),
        selectRow: (id) => table("selectRow", { id }),
        selectPage: (label) => table("selectPage", { label }),
      },
    }),
    [confirmation, table, shellHeader, primitives]
  );

  return (
    <UIProvider link={Link} messages={messages}>
      {children}
    </UIProvider>
  );
}
