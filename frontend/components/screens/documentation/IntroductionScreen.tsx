import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

const strong = (chunks: ReactNode) => <strong className="text-foreground">{chunks}</strong>;
function docLink(href: string) {
  return function DocLink(chunks: ReactNode) {
    return (
      <Link href={href} className="text-foreground underline">
        {chunks}
      </Link>
    );
  };
}

export function IntroductionScreen() {
  const t = useTranslations("docsIntroduction");
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t("tagline")}</p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("whatTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("whatBody")}</p>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("whatBody2")}</p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("conceptsTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>{t.rich("conceptOrgs", { strong })}</li>
          <li>{t.rich("conceptApps", { strong })}</li>
          <li>{t.rich("conceptEnvironments", { strong })}</li>
          <li>{t.rich("conceptDeployments", { strong })}</li>
          <li>{t.rich("conceptRoles", { strong })}</li>
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("runtimeTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("runtimeBody")}</p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("startTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>{t.rich("startQuickstart", { link: docLink("/documentation/quickstart") })}</li>
          <li>
            {t.rich("startPrerequisites", {
              link: docLink("/documentation/cluster-prerequisites"),
            })}
          </li>
          <li>{t.rich("startLocalDev", { link: docLink("/documentation/get-started") })}</li>
        </ul>
      </section>
    </article>
  );
}
