import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

function docLink(href: string) {
  return function DocLink(chunks: ReactNode) {
    return (
      <Link href={href} className="text-foreground underline">
        {chunks}
      </Link>
    );
  };
}

export function GetStartedScreen() {
  const t = useTranslations("docsGetStarted");
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t("intro")}</p>
      </div>

      <div className="bg-muted/40 text-muted-foreground rounded-md border p-4 text-sm leading-relaxed">
        {t.rich("note", {
          quickstart: docLink("/documentation/quickstart"),
          configuration: docLink("/documentation/configuration"),
        })}
      </div>

      <section className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">{t("prepareTitle")}</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">{t("prepareBody")}</p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./bootstrap.sh
./run.sh up
./run.sh ps`}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">{t("migrateTitle")}</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">{t("migrateBody")}</p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./run.sh migrate
./run.sh seed`}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">{t("exploreTitle")}</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t("exploreBody", {
              ui: "http://localhost:3000",
              api: "http://localhost:8000/app/gql/config/",
              temporal: "http://localhost:8233",
            })}
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./run.sh urls
./run.sh logs
./run.sh shell`}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">{t("checksTitle")}</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">{t("checksBody")}</p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./run.sh test
make lint
make schema
cd frontend
npm run codegen
npm run typecheck
npm test`}</code>
          </pre>
        </div>
      </section>
    </article>
  );
}
