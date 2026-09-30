"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { PlaygroundScreenProps } from "./playground.types";
export type { PlaygroundScreenProps } from "./playground.types";

/** Pure prompt surface: endpoint pages and actual relay state arrive as props. */
export function PlaygroundScreen(p: PlaygroundScreenProps) {
  const t = useTranslations("playground");
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">{t("title")}</h1>
        <nav className="flex flex-wrap gap-3 text-sm" aria-label={t("savedNavigation")}>
          <Link className="underline" href="/playground/history">
            {t("history")}
          </Link>
          <Link className="underline" href="/playground/starred">
            {t("starred")}
          </Link>
          <Link className="underline" href="/models">
            {t("models")}
          </Link>
        </nav>
      </header>
      <p className="text-muted-foreground text-sm">{t("singlePrompt")}</p>
      <div className="grid min-w-0 gap-4 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="flex min-w-0 flex-col gap-3">
          <h2 className="font-medium">{t("savedSessions")}</h2>
          <p className="text-muted-foreground text-xs">{t("localOnly")}</p>
          <Button variant="outline" onClick={p.onNew} disabled={p.loading}>
            {t("new")}
          </Button>
          {p.savedLoading && <p role="status">{t("localOnly")} …</p>}
          {!p.savedLoading && p.savedSessions.length === 0 && (
            <p className="text-muted-foreground text-sm">{t("nothingSaved")}</p>
          )}
          <ul className="flex flex-col gap-2">
            {p.savedSessions.map((s) => (
              <li
                key={s.id}
                className={cn("rounded border p-2", p.activeSavedId === s.id && "bg-accent")}
              >
                <button
                  type="button"
                  onClick={() => p.onLoad(s.id)}
                  disabled={p.loading}
                  className="w-full truncate text-left text-sm"
                >
                  {s.title}
                </button>
                <div className="text-muted-foreground truncate text-xs">{s.modelName}</div>
                <div className="mt-2 flex gap-2">
                  <Button size="sm" variant="ghost" onClick={() => p.onStar(s.id)}>
                    {t(s.starred ? "unstar" : "star")}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => p.onDelete(s.id)}>
                    {t("delete")}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </aside>
        <section className="flex min-w-0 flex-col gap-4">
          <section
            className="flex min-w-0 flex-col gap-3 rounded border p-4"
            aria-label={t("endpoint")}
          >
            <label className="text-sm" htmlFor="playground-endpoint-search">
              {t("searchEndpoints")}
            </label>
            <Input
              id="playground-endpoint-search"
              value={p.search}
              onChange={(e) => p.setSearch(e.target.value)}
              disabled={p.loading}
            />
            {p.catalogLoading ? (
              <p role="status">{t("loadingEndpoints")}</p>
            ) : p.catalogError ? (
              <div role="alert">
                {t("catalogFailed")}{" "}
                <Button variant="outline" onClick={p.onCatalogRetry}>
                  {t("retry")}
                </Button>
              </div>
            ) : (
              <>
                {p.models.length === 0 ? (
                  <p>{t("noEndpoints")}</p>
                ) : (
                  <div className="flex flex-col gap-2">
                    {p.models.map((m) => (
                      <Button
                        key={m.id}
                        variant={p.model === m.id ? "secondary" : "outline"}
                        onClick={() => p.setModel(m.id)}
                        disabled={p.loading}
                        className="h-auto justify-start text-left break-words whitespace-normal"
                        aria-pressed={p.model === m.id}
                      >
                        {m.name} · {m.variant} · {m.registeredAppSlug ?? t("projectOwned")} /{" "}
                        {m.environmentName ?? t("unknown")}
                      </Button>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    disabled={p.page <= 1 || p.loading}
                    onClick={() => p.setPage(p.page - 1)}
                  >
                    {t("previous")}
                  </Button>
                  <span className="text-sm">
                    {t("page", { page: p.page, total: p.totalCount })}
                  </span>
                  <Button
                    variant="outline"
                    disabled={p.page * 10 >= p.totalCount || p.loading}
                    onClick={() => p.setPage(p.page + 1)}
                  >
                    {t("next")}
                  </Button>
                </div>
              </>
            )}
            <p className="text-sm break-words">
              {t("selected", { name: p.modelName || t("none") })}
            </p>
            <p
              role={p.readiness === "loading" ? "status" : "note"}
              className="text-muted-foreground text-sm"
            >
              {t(`readiness.${p.readiness}`)}
            </p>
            {p.model && p.readiness !== "loading" && (
              <Button variant="outline" onClick={p.onReadinessRetry} disabled={p.loading}>
                {t("checkReadiness")}
              </Button>
            )}
            {p.readiness === "READY" && (
              <p className="text-muted-foreground text-xs">
                {t("relayAdvisory", {
                  chars: p.maxPromptChars,
                  tokens: p.maxOutputTokens,
                  seconds: p.maxWaitSeconds,
                })}
              </p>
            )}
          </section>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={p.tab === "chat" ? "default" : "outline"}
              onClick={() => p.setTab("chat")}
              disabled={p.loading}
            >
              {t("chat")}
            </Button>
            <Button
              variant={p.tab === "batch" ? "default" : "outline"}
              onClick={() => p.setTab("batch")}
              disabled={p.loading}
            >
              {t("batch")}
            </Button>
            <Input
              aria-label={t("sessionTitle")}
              value={p.title}
              onChange={(e) => p.setTitle(e.target.value)}
              maxLength={120}
              placeholder={t("sessionTitle")}
              className="min-w-0 flex-1"
            />
            <Button variant="outline" onClick={p.onSave} disabled={!p.messages.length || p.loading}>
              {t("save")}
            </Button>
            <Button
              variant="outline"
              onClick={p.onShare}
              disabled={!p.messages.length || p.loading}
            >
              {t("copySession")}
            </Button>
          </div>
          {p.tab === "batch" ? (
            p.batch
          ) : (
            <>
              <div className="flex min-h-40 flex-col gap-3 rounded border p-4" aria-live="polite">
                {p.messages.length === 0 && (
                  <p className="text-muted-foreground text-sm">{t("emptyChat")}</p>
                )}
                {p.messages.map((m, i) => (
                  <div
                    key={i}
                    className={cn(
                      "rounded p-3 text-sm",
                      m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
                    )}
                  >
                    <p className="font-medium">{t(m.role === "user" ? "you" : "modelReply")}</p>
                    <p className="break-words whitespace-pre-wrap">{m.content}</p>
                    {(m.totalTokens != null || m.latencyMs != null) && (
                      <p className="mt-2 text-xs">
                        {t("observed", {
                          tokens: m.totalTokens ?? t("unknown"),
                          latency: m.latencyMs ?? t("unknown"),
                        })}
                      </p>
                    )}
                  </div>
                ))}
                {p.loading && <p role="status">{t("sending")}</p>}
              </div>
              {p.error && <p role="alert">{t(`errors.${p.error}`)}</p>}
              <label className="text-sm" htmlFor="playground-prompt">
                {t("prompt")}
              </label>
              <Textarea
                id="playground-prompt"
                value={p.prompt}
                onChange={(e) => p.setPrompt(e.target.value)}
                disabled={p.loading}
                rows={3}
                maxLength={p.maxPromptChars}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    p.onSend();
                  }
                }}
              />
              <Button
                onClick={p.onSend}
                disabled={
                  !p.canSend ||
                  p.loading ||
                  !p.prompt.trim() ||
                  p.prompt.trim().length > p.maxPromptChars
                }
              >
                {t("send")}
              </Button>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
