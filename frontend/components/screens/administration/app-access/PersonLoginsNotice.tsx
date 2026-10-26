"use client";

import { useTranslations } from "next-intl";

import { StatusDot } from "@/components/StatusDot";

import type { PersonLogin, PersonSummary } from "./edge-access-list";

function loginLine(login: PersonLogin, t: ReturnType<typeof useTranslations>): string {
  if (!login.resolved) return t("unresolved", { reason: login.reason });
  if (!login.hasLogin) return t("noLogin");
  if (login.enabled === false) return t("disabled");
  return login.groups.length ? t("groups", { groups: login.groups.join(", ") }) : t("noGroups");
}

/**
 * Who the person is to each cluster's identity provider, above their list
 * (#2534): their groups, no login, or why the provider could not be read,
 * so an unknown row says why. And how many environments refuse them.
 */
export function PersonLoginsNotice({ person }: { person: PersonSummary }) {
  const t = useTranslations("adminAppAccess.person");
  return (
    <div className="bg-surface-1 flex min-w-0 flex-col gap-1.5 rounded-sm border px-3 py-2 text-xs">
      <p className="min-w-0 [overflow-wrap:anywhere]">
        {t("title", { email: person.email })}
        {person.refusedCount > 0 && (
          <span className="text-muted-foreground">
            {" "}
            · {t("refused", { count: person.refusedCount })}
          </span>
        )}
      </p>
      {person.logins.length === 0 ? (
        <p className="text-muted-foreground">{t("noEdge")}</p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-1">
          {person.logins.map((login) => (
            <li key={login.clusterSlug} className="flex min-w-0 items-center gap-1.5">
              <StatusDot
                status={
                  !login.resolved
                    ? "warn"
                    : login.hasLogin && login.enabled !== false
                      ? "ok"
                      : "muted"
                }
              />
              <span className="font-mono">{login.clusterSlug}</span>
              <span className="text-muted-foreground truncate" title={loginLine(login, t)}>
                {loginLine(login, t)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
