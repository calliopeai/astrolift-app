"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import type { CopiedListDefinition, ListDefinition } from "./list-state";

type Translate = ReturnType<typeof useTranslations>;

/**
 * A list declaration in the viewer's language (#2381). Labels only: keys,
 * option values, view filters, sort and paging stay as declared. The copy is
 * `search`, `fields.<key>`, `views.<key>`, `notes.<view key>` and, for an
 * option that is a word rather than data, `options.<field key>.<value>`; an
 * option with no message keeps its declared label (a host, an adapter id).
 */
export function localizedList(def: ListDefinition, t: Translate): ListDefinition {
  return {
    ...def,
    searchPlaceholder: def.searchable === false ? def.searchPlaceholder : t("search"),
    fields: def.fields.map((field) => ({
      ...field,
      label: t(`fields.${field.key}`),
      options: field.options?.map((option) => {
        const key = `options.${field.key}.${option.value}`;
        return t.has(key) ? { ...option, label: t(key) } : option;
      }),
    })),
    views: def.views.map((view) => ({
      ...view,
      label: t(`views.${view.key}`),
      ...(view.note === undefined ? {} : { note: t(`notes.${view.key}`) }),
    })),
  };
}

/** `localizedList` from `listDefinitions.<def.copy>`, stable while the locale is. */
export function useLocalizedList(def: CopiedListDefinition): ListDefinition {
  const t = useTranslations(`listDefinitions.${def.copy}`);
  return React.useMemo(() => localizedList(def, t), [def, t]);
}
