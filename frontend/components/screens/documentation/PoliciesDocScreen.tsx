import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

const code = (chunks: ReactNode) => <code>{chunks}</code>;
const strong = (chunks: ReactNode) => <strong>{chunks}</strong>;
const strongTerm = (chunks: ReactNode) => <strong className="text-foreground">{chunks}</strong>;
const em = (chunks: ReactNode) => <em>{chunks}</em>;
function docLink(href: string) {
  return function DocLink(chunks: ReactNode) {
    return (
      <Link href={href} className="text-foreground underline-offset-2 hover:underline">
        {chunks}
      </Link>
    );
  };
}

export function PoliciesDocScreen() {
  const t = useTranslations("docsPolicies");
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t.rich("intro", { strong, em })}</p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("whenTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("whenBody")}</p>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>{t.rich("exampleSenior", { code })}</li>
          <li>{t("exampleNight")}</li>
          <li>{t("exampleMfa")}</li>
          <li>{t("exampleContractors")}</li>
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("prerequisitesTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>{t.rich("prereqPermission", { code, strong: strongTerm })}</li>
          <li>{t.rich("prereqModel", { em, strong: strongTerm })}</li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("createTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("createBody", { strong, link: docLink("/administration/policies") })}
        </p>
      </section>

      <div className="grid gap-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t("fieldScope")}{" "}
              <Badge variant="outline" className="ml-1">
                ORG · TEAM · PROJECT · APP
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            {t("fieldScopeBody")}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t("fieldEffect")}{" "}
              <Badge className="bg-success/15 text-success-fg ml-1" variant="secondary">
                ALLOW
              </Badge>{" "}
              <Badge className="bg-danger/15 text-danger-fg ml-1" variant="secondary">
                DENY
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            {t("fieldEffectBody")}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t("fieldAction")}</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("fieldActionBody", { code })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t("fieldResource")}</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("fieldResourceBody", { code })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t("fieldActor")}</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground flex flex-col gap-3 text-sm leading-relaxed">
            <span>{t.rich("fieldActorBody", { code, empty: "{}" })}</span>
            <pre className="bg-muted overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`{ "role_slug": ["senior-eng"] }
{ "user_id": ["usr_01HW…"] }
{ "role_slug": ["contractor"], "team_slug": ["payments"] }`}</code>
            </pre>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t("fieldConditions")}</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground flex flex-col gap-3 text-sm leading-relaxed">
            <span>{t.rich("fieldConditionsBody", { code })}</span>
            <pre className="bg-muted overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`{
  "time_of_day": { "after": "22:00", "before": "06:00", "tz": "UTC" },
  "ip_cidr": ["10.0.0.0/8"]
}`}</code>
            </pre>
          </CardContent>
        </Card>
      </div>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("exampleSeniorTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("exampleSeniorBody", { code })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`Scope:    ORG
Effect:   DENY
Action:   app.deploy
Resource: app:prod-*
Actor:    {}             # applies to all actors
Conditions:
  { "role_slug_not_in": ["senior-eng"] }`}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("exampleSeniorOutcome", { code })}
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("exampleFreezeTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("exampleFreezeBody")}</p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`Scope:    ORG
Effect:   DENY
Action:   secret.write
Resource: *
Actor:    {}
Conditions:
  { "time_of_day": { "after": "22:00", "before": "06:00", "tz": "UTC" } }`}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("troubleshootingTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">{t("troubleEffectTitle")}</strong>{" "}
            {t("troubleEffectBody")}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleButtonTitle")}</strong>{" "}
            {t.rich("troubleButtonBody", { link: docLink("/administration/permissions") })}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleJsonTitle")}</strong>{" "}
            {t.rich("troubleJsonBody", { code })}
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("relatedTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            {t.rich("relatedIdentityProviders", {
              link: docLink("/documentation/identity-providers"),
            })}
          </li>
          <li>{t.rich("relatedDiagnostic", { link: docLink("/administration/permissions") })}</li>
        </ul>
      </section>
    </article>
  );
}
