import { localizedScopeLabel, type AccessTranslator } from "./access-copy";
import type { RoleRef, ScopeRef } from "./access-model";
import type { GrantPreview } from "./GrantAccessFlow";

type GrantTranslator = (
  key: "effect" | "access" | "where",
  values?: Record<string, string | number>
) => string;

/** Counts are authoritative preview counts; names and unknown kinds stay literal. */
export function localizedGrantEffect(
  preview: GrantPreview,
  role: RoleRef | null,
  scope: ScopeRef | null,
  t: GrantTranslator,
  accessT: AccessTranslator
): string {
  return t("effect", {
    count: preview.gainingCount ?? preview.gaining.length,
    already: preview.alreadyCount ?? preview.already.length,
    what: role?.name ?? t("access"),
    where: scope
      ? t("where", { kind: localizedScopeLabel(scope.kind, accessT), name: scope.name })
      : "",
  });
}
