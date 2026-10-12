import Link from "next/link";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export interface ChangelogEntry {
  version: string;
  date: string;
  type: string;
  changes: string[];
}

const typeVariant = (type: string): "default" | "secondary" | "outline" =>
  type === "feature" ? "default" : type === "fix" ? "secondary" : "outline";

export interface ChangelogScreenProps {
  entries?: ChangelogEntry[];
}

/** Release notes, most recent first. */
export function ChangelogScreen({ entries = [] }: ChangelogScreenProps) {
  const t = useTranslations("docsChangelog");
  const typeLabel = (type: string) =>
    type === "feature" || type === "fix" ? t(`types.${type}`) : type;
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t("intro")}</p>
      </div>

      {entries.length === 0 && (
        <div className="text-muted-foreground space-y-3 text-sm">
          <p>{t("unavailable")}</p>
          <p>
            {t.rich("repository", {
              link: (chunks) => (
                <Link
                  href="https://github.com/calliopeai/astrolift-app/blob/main/CHANGELOG.md"
                  target="_blank"
                  rel="noreferrer"
                  className="text-foreground underline"
                >
                  {chunks}
                </Link>
              ),
            })}
          </p>
        </div>
      )}
      <div className="flex flex-col gap-8">
        {entries.map((entry, i) => (
          <div key={entry.version}>
            {i > 0 && <Separator className="mb-8" />}
            <div className="mb-3 flex items-center gap-3">
              <span className="text-base font-semibold">v{entry.version}</span>
              <Badge variant={typeVariant(entry.type)}>{typeLabel(entry.type)}</Badge>
              <span className="text-muted-foreground ml-auto text-xs">{entry.date}</span>
            </div>
            <ul className="flex flex-col gap-1.5">
              {entry.changes.map((c) => (
                <li key={c} className="text-muted-foreground flex gap-2 text-sm">
                  <span className="bg-muted-foreground mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" />
                  {c}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </article>
  );
}
