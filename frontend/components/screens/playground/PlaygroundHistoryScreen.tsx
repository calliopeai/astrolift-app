"use client";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { latestObservedPrompt, type SavedSession } from "./saved-sessions";
export type PlaygroundHistorySession = SavedSession;

/** Bounded browser-local records, never fabricated global invocation history. */
export function PlaygroundHistoryScreen({
  sessions,
  loading = false,
  error = false,
  onRetry = () => {},
}: {
  sessions: PlaygroundHistorySession[];
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
}) {
  const t = useTranslations("playground");
  const f = useFormatter();
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">{t("history")}</h1>
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
      ) : !sessions.length ? (
        <p>{t("nothingSaved")}</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {sessions.map((s) => {
            const { prompt, reply } = latestObservedPrompt(s.messages);
            return (
              <li key={s.id} className="min-w-0 rounded border p-4">
                <Link
                  href={`/playground?session=${encodeURIComponent(s.id)}`}
                  className="font-medium break-words underline"
                >
                  {s.title}
                </Link>
                <p className="text-muted-foreground text-sm break-words">
                  {s.modelName} ·{" "}
                  {f.dateTime(new Date(s.updatedAt), { dateStyle: "medium", timeStyle: "short" })}
                </p>
                <p className="mt-2 text-sm break-words whitespace-pre-wrap">{prompt?.content}</p>
                {reply && (
                  <p className="text-muted-foreground mt-2 text-xs">
                    {t("observed", {
                      tokens: reply.totalTokens ?? t("unknown"),
                      latency: reply.latencyMs ?? t("unknown"),
                    })}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
