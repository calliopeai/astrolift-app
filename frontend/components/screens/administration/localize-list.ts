import type { useTranslations } from "next-intl";

import type { ListDefinition } from "@/components/list/list-state";

/**
 * Labels only: field, option, view and search copy come from the list's
 * message namespace; keys, option values and filters stay literal.
 *
 *   fields.<key>, views.<key>, notes.<view key>, search, and
 *   options.<field key>.<value> for the fields named in `optionFields`.
 */
export function localizeList(
  def: ListDefinition,
  t: ReturnType<typeof useTranslations>,
  optionFields: readonly string[] = []
): ListDefinition {
  return {
    ...def,
    searchPlaceholder: t("search"),
    fields: def.fields.map((field) => ({
      ...field,
      label: t(`fields.${field.key}`),
      options:
        field.options && optionFields.includes(field.key)
          ? field.options.map((option) => ({
              ...option,
              label: t(`options.${field.key}.${option.value}`),
            }))
          : field.options,
    })),
    views: def.views.map((view) => ({
      ...view,
      label: t(`views.${view.key}`),
      note: view.note ? t(`notes.${view.key}`) : view.note,
    })),
  };
}
