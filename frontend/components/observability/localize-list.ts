import type { useTranslations } from "next-intl";

import type { ListDefinition } from "@/components/list/list-state";

/**
 * A panel list declaration in the viewer's language. Copy lives under
 * `obsLists.<list>`: `searchPlaceholder`, `fields.<key>.label`,
 * `fields.<key>.options.<value>` (only where an option is a word, not data
 * such as a record type) and `mineNote`; the view tabs share `obsLists.views`.
 */
export function localizeList(
  def: ListDefinition,
  list: string,
  t: ReturnType<typeof useTranslations<"obsLists">>
): ListDefinition {
  return {
    ...def,
    searchPlaceholder: t(`${list}.searchPlaceholder`),
    fields: def.fields.map((field) => ({
      ...field,
      label: t(`${list}.fields.${field.key}.label`),
      options: field.options?.map((option) => {
        const key = `${list}.fields.${field.key}.options.${option.value}`;
        return { ...option, label: t.has(key) ? t(key) : option.label };
      }),
    })),
    views: def.views.map((view) => ({
      ...view,
      label: t(`views.${view.key}`),
      note: view.note === undefined ? undefined : t(`${list}.mineNote`),
    })),
  };
}
