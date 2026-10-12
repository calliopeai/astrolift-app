import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

import { IDENTITY_PROVIDER_KINDS, type IdentityProviderKind } from "./identity-provider-kinds";

export type IdentityProvidersDocProps = {
  /** Already-localized kinds; defaults to the built-in list. */
  kinds?: IdentityProviderKind[];
};

const code = (chunks: ReactNode) => <code>{chunks}</code>;
const strong = (chunks: ReactNode) => <strong>{chunks}</strong>;
const strongTerm = (chunks: ReactNode) => <strong className="text-foreground">{chunks}</strong>;
function docLink(href: string) {
  return function DocLink(chunks: ReactNode) {
    return (
      <Link href={href} className="text-foreground underline-offset-2 hover:underline">
        {chunks}
      </Link>
    );
  };
}

/** Static "Identity providers" documentation page. */
export function IdentityProvidersDoc({ kinds }: IdentityProvidersDocProps) {
  const t = useTranslations("docsIdentityProviders");
  const resolved: IdentityProviderKind[] =
    kinds ??
    IDENTITY_PROVIDER_KINDS.map(({ id, key, badge, steps }) => ({
      id,
      title: t(`kinds.${key}.title`),
      badge: badge ?? t(`kinds.${key}.badge`),
      intro: t(`kinds.${key}.intro`),
      steps: steps.map((step) => t(`kinds.${key}.steps.${step.key}`, step.values)),
    }));
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t("intro")}</p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("whenTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("whenBody")}</p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("prerequisitesTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>{t.rich("prereqPermission", { code, strong: strongTerm })}</li>
          <li>{t.rich("prereqAdmin", { strong: strongTerm })}</li>
          <li>
            {t.rich("prereqRedirect", {
              code,
              strong: strongTerm,
              bold: strong,
              uri: "https://<your-install>/app/auth1/callback",
            })}
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("configureTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("configureBody", { strong, link: docLink("/settings/identity-provider") })}
        </p>
      </section>

      {resolved.map((k) => (
        <section key={k.id} id={k.id} className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-medium">{k.title}</h3>
            <Badge variant="outline">{k.badge}</Badge>
          </div>
          <p className="text-muted-foreground text-sm leading-relaxed">{k.intro}</p>
          <ol className="text-muted-foreground flex flex-col gap-2 pl-5 text-sm">
            {k.steps.map((s, i) => (
              <li key={i} className="list-decimal leading-relaxed">
                {s}
              </li>
            ))}
          </ol>
        </section>
      ))}

      <Separator />

      <section id="domain-allowlist" className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("allowlistTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("allowlistBody")}</p>
        <ol className="text-muted-foreground flex flex-col gap-2 pl-5 text-sm">
          <li className="list-decimal leading-relaxed">
            {t.rich("allowlistStep1", { link: docLink("/administration/organization") })}
          </li>
          <li className="list-decimal leading-relaxed">
            {t.rich("allowlistStep2", { code, strong })}
          </li>
          <li className="list-decimal leading-relaxed">{t.rich("allowlistStep3", { code })}</li>
        </ol>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("troubleshootingTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">{t("troubleNoIdpTitle")}</strong>{" "}
            {t.rich("troubleNoIdpBody", { code, link: docLink("/settings/identity-provider") })}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleRedirectTitle")}</strong>{" "}
            {t.rich("troubleRedirectBody", { code })}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleSignupTitle")}</strong>{" "}
            {t.rich("troubleSignupBody", { link: docLink("#domain-allowlist") })}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleSamlTitle")}</strong>{" "}
            {t("troubleSamlBody")}
          </li>
        </ul>
      </section>
    </article>
  );
}
