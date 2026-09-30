"use client";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import type { SavedSession } from "./saved-sessions";
export type PlaygroundStarredPrompt = SavedSession;

/** Saved local starred sessions; selecting one never invokes a model. */
export function PlaygroundStarredScreen({
  items,
  loading = false,
  error = false,
  onRetry = () => {},
}: {
  items: PlaygroundStarredPrompt[];
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
}) {
  const t = useTranslations("playground");
  const f = useFormatter();
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">{t("starred")}</h1>
        <Link className="underline" href="/playground">
          {t("title")}
        </Link>
      </header>
      <p className="text-muted-foreground text-sm">{t("localOnly")}</p>
      {loading ? (
        <p role="status">{t("localOnly")} …</p>
      ) : error ? (
        <div role="alert">
          {t("loadFailed")}{" "}
          <Button variant="outline" onClick={onRetry}>
            {t("retry")}
          </Button>
        </div>
      ) : !items.length ? (
        <p>{t("nothingSaved")}</p>
      ) : (
        <ul className="grid min-w-0 gap-4 sm:grid-cols-2">
          {items.map((s) => (
            <li key={s.id} className="min-w-0 rounded border p-4">
              <Link
                href={`/playground?session=${encodeURIComponent(s.id)}`}
                className="font-medium break-words underline"
              >
                {s.title}
              </Link>
              <p className="text-muted-foreground mt-2 text-sm break-words">
                {s.messages.find((m) => m.role === "user")?.content}
              </p>
              <p className="text-muted-foreground mt-2 text-xs break-words">
                {s.modelName} · {f.dateTime(new Date(s.updatedAt), { dateStyle: "medium" })}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
