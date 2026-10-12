import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

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

export function SourceProvidersDocScreen() {
  const t = useTranslations("docsSourceProviders");
  const step = (n: number, title: string) => t("stepHeading", { n, title });
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t.rich("intro", { code })}</p>
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
          <li>{t.rich("prereqUrl", { code, strong: strongTerm })}</li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">
          GitHub{" "}
          <Badge className="ml-1" variant="secondary">
            {t("recommendedBadge")}
          </Badge>
        </h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("githubIntro")}</p>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(1, t("openProvidersTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("openProvidersGitHub", {
              strong,
              link: docLink("/settings/source-providers"),
            })}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(2, t("githubOrgTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("githubOrgBody", { code, strong })}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(3, t("githubApproveTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">{t("githubApproveBody")}</p>
          <ul className="text-muted-foreground flex flex-col gap-1 text-sm">
            <li>{t.rich("permContents", { code })}</li>
            <li>{t.rich("permMetadata", { code })}</li>
            <li>{t.rich("permPullRequests", { code })}</li>
            <li>{t.rich("permChecks", { code })}</li>
          </ul>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("githubCreate", { strong, button: "Create GitHub App for <your org>" })}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(4, t("githubReposTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("githubReposBody", {
              code,
              strong,
              link: docLink("/apps/new"),
              url: "https://github.com/organizations/<org>/settings/installations",
            })}
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">
          GitLab <Badge variant="outline">{t("guidedManualBadge")}</Badge>
        </h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("gitlabIntro")}</p>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(1, t("openProvidersTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("openProvidersGitLab", {
              strong,
              link: docLink("/settings/source-providers"),
            })}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(2, t("gitlabGroupTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("gitlabGroupBody", { code })}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(3, t("gitlabRegisterTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("gitlabRegisterBody", { strong })}
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`Name:          Astrolift
Redirect URI:  <copy from the Astrolift dialog>
Scopes:        api, read_repository, write_repository`}</code>
          </pre>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("gitlabSaveBody", { strong })}
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{step(4, t("gitlabPasteTitle"))}</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t.rich("gitlabPasteBody", { strong })}
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("troubleshootingTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              &ldquo;Hook url is not supported because it isn&apos;t reachable over the public
              Internet.&rdquo;
            </strong>{" "}
            {t.rich("troubleHookBody", { code })}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleReposTitle")}</strong>{" "}
            {t.rich("troubleReposBody", {
              code,
              strong,
              url: "github.com/organizations/<org>/settings/installations",
            })}
          </li>
          <li>
            <strong className="text-foreground">{t("troublePushTitle")}</strong>{" "}
            {t.rich("troublePushBody", { strong, link: docLink("/settings/source-providers") })}
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("relatedTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>{t.rich("relatedWebhooks", { link: docLink("/documentation/webhooks") })}</li>
          <li>
            {t.rich("relatedCustomDomains", { link: docLink("/documentation/custom-domains") })}
          </li>
        </ul>
      </section>
    </article>
  );
}
