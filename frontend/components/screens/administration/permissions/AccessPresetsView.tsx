"use client";

import { CheckIcon, MinusIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";

import { grantHref } from "../access/access-nav";
import { permissionsCrumbs } from "../access/admin-crumbs";
import type { useAccessPresets } from "./use-access-presets";

export type AccessPresetsViewProps = ReturnType<typeof useAccessPresets> & {
  /** Guided deploy approval (`DeployApprovalSetup`), mounted by the route. */
  deployApproval?: React.ReactNode;
};

/** Where the org's model-connection policy lives (#2270); there is no Model consumer preset. */
export const MODEL_CONNECTION_POLICY_HREF =
  "/administration/organization?section=model-connections";

const SCOPES = ["ORG", "TEAM", "PROJECT", "APP"];

/**
 * Admin › Permissions › Easy setup (#2272): the plain-language starting
 * points. Each preset is a stock role per scope, and the table shows what that
 * role allows and leaves out, as the server computes it from the role's
 * permissions. Assigning one is an ordinary assignment of that role. Below
 * them, guided deploy approval and, in place of a Model consumer preset, the
 * way to the org's model-connection policy.
 */
export function AccessPresetsView({
  presets,
  loading,
  error,
  onRetry,
  deployApproval,
}: AccessPresetsViewProps) {
  const t = useTranslations("adminPresets");
  const scopeLabel = (level: string) => (SCOPES.includes(level) ? t(`scopes.${level}`) : level);
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader crumbs={permissionsCrumbs("presets")} title={t("title")} />
      <p className="text-muted-foreground max-w-prose text-sm">{t("intro")}</p>
      {loading ? (
        <p role="status" className="text-muted-foreground text-sm">
          {t("loading")}
        </p>
      ) : error ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>{t("loadError", { error })}</p>
          <Button size="sm" variant="outline" onClick={onRetry}>
            {t("retry")}
          </Button>
        </div>
      ) : (
        <div className="space-y-6">
          {(presets ?? []).map((preset) => (
            <section key={preset.key} aria-label={preset.label} className="space-y-2">
              <div>
                <h2 className="font-semibold">{preset.label}</h2>
                <p className="text-muted-foreground text-sm">{preset.summary}</p>
              </div>
              <div className="overflow-x-auto rounded-sm border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                        {t("task")}
                      </th>
                      {preset.roles.map((role) => (
                        <th key={role.scopeLevel} scope="col" className="p-2 text-left font-medium">
                          {scopeLabel(role.scopeLevel)}
                          <span className="text-muted-foreground block font-mono text-xs font-normal">
                            {role.roleSlug}
                          </span>
                          <Link
                            className="text-xs font-normal underline underline-offset-2"
                            href={grantHref({
                              role: role.roleSlug,
                              returnTo: "/administration/permissions/presets",
                            })}
                            aria-label={t("grantAt", {
                              preset: preset.label,
                              scope: scopeLabel(role.scopeLevel),
                            })}
                          >
                            {t("grant")}
                          </Link>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(preset.roles[0]?.tasks ?? []).map((task, row) => (
                      <tr key={task.key} className="border-b last:border-0">
                        <th scope="row" className="p-2 text-left font-normal">
                          {task.label}
                        </th>
                        {preset.roles.map((role) => {
                          const granted = role.tasks[row]?.granted === true;
                          return (
                            <td key={role.scopeLevel} className="p-2">
                              {granted ? (
                                <CheckIcon
                                  className="text-success size-4"
                                  aria-label={t("allowed")}
                                />
                              ) : (
                                <MinusIcon
                                  className="text-muted-foreground size-4"
                                  aria-label={t("notIncluded")}
                                />
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
          <p className="text-sm">
            {t.rich("footer", {
              link: (chunks) => (
                <Link
                  className="underline underline-offset-2"
                  href="/administration/permissions/assignments"
                >
                  {chunks}
                </Link>
              ),
            })}
          </p>
        </div>
      )}
      {deployApproval}
      <section aria-labelledby="model-access" className="space-y-1">
        <h2 id="model-access" className="font-semibold">
          {t("models.title")}
        </h2>
        <p className="text-muted-foreground max-w-prose text-sm">{t("models.body")}</p>
        <Link className="text-sm underline underline-offset-2" href={MODEL_CONNECTION_POLICY_HREF}>
          {t("models.link")}
        </Link>
      </section>
    </div>
  );
}
