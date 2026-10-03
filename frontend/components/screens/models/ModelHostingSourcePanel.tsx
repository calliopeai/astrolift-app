"use client";

import { useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type HostingConnection = {
  id: string;
  version: number;
  name: string;
  accountUsername: string;
};
export interface ModelHostingSourceProps {
  scopeKey: string;
  allowed: boolean | null;
  authorityError: string | null;
  onRetryAuthority: () => void;
  connections: HostingConnection[];
  selectedConnection: HostingConnection | null;
  connectionsLoading: boolean;
  connectionsError: string | null;
  connectionPage: number;
  connectionPages: number;
  onConnectionPage: (page: number) => void;
  onRetryConnections: () => void;
  onSelectConnection: (id: string | null) => void;
  onConnect: (
    name: string,
    token: string
  ) => Promise<
    | { accepted: true; account: string | null; current: boolean; refreshFailed: boolean }
    | { accepted: false; message: string }
  >;
  onManualSource: (model: { repoId: string; revisionSha: string }) => void;
}

export function ModelHostingSourcePanel(props: ModelHostingSourceProps) {
  return <SourcePanel key={props.scopeKey} {...props} />;
}

function SourcePanel(props: ModelHostingSourceProps) {
  const t = useTranslations("models.shared.hosting");
  const id = useId();
  const [name, setName] = useState("");
  const [token, setToken] = useState("");
  const [repo, setRepo] = useState("");
  const [revision, setRevision] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [invalidSource, setInvalidSource] = useState(false);
  const latest = useRef(props);
  useLayoutEffect(() => {
    latest.current = props;
  }, [props]);
  const active = useRef(true);
  useLayoutEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  // scopeKey changes remount this component. An old callback must not mutate
  // the new organization's credential draft or selection.
  const connect = async () => {
    if (connecting || props.allowed !== true || latest.current.scopeKey !== props.scopeKey) return;
    setConnecting(true);
    setFeedback(null);
    setWarning(null);
    try {
      const result = await props.onConnect(name, token);
      if (!active.current || latest.current.scopeKey !== props.scopeKey) return;
      if (result.accepted) {
        setToken("");
        setFeedback(
          result.account ? t("connected", { account: result.account }) : t("connectionSaved")
        );
        setWarning(
          !result.current ? t("changed") : result.refreshFailed ? t("savedRefreshFailed") : null
        );
      } else setFeedback(result.message);
    } catch {
      if (active.current && latest.current.scopeKey === props.scopeKey)
        setFeedback(t("connectFailed"));
    } finally {
      if (active.current && latest.current.scopeKey === props.scopeKey) setConnecting(false);
    }
  };
  const selected = props.selectedConnection;
  const connections =
    selected && !props.connections.some((row) => row.id === selected.id)
      ? [selected, ...props.connections]
      : props.connections;
  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">{t("sourceStep")}</h2>
      <p className="text-muted-foreground text-sm">{t("sourceHelp")}</p>
      {props.allowed !== true && (
        <div role="status" className="space-y-2">
          <p>
            {props.authorityError ?? t(props.allowed === null ? "adminChecking" : "adminRequired")}
          </p>
          <Button type="button" variant="outline" onClick={props.onRetryAuthority}>
            {t("retry")}
          </Button>
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor={`${id}-connection`}>{t("connection")}</Label>
        <select
          id={`${id}-connection`}
          className="border-input bg-background w-full rounded-md border px-3 py-2 text-sm"
          value={selected?.id ?? ""}
          disabled={
            props.allowed !== true || props.connectionsLoading || Boolean(props.connectionsError)
          }
          onChange={(event) => props.onSelectConnection(event.target.value || null)}
        >
          <option value="">{t("anonymous")}</option>
          {connections.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name} · {row.accountUsername}
            </option>
          ))}
        </select>
        {props.connectionsLoading && <p role="status">{t("connectionsLoading")}</p>}
        {!props.connectionsLoading &&
          !props.connectionsError &&
          props.allowed === true &&
          !connections.length && <p role="status">{t("noConnections")}</p>}
        {props.connectionsError && (
          <div role="alert">
            <p>{props.connectionsError}</p>
            <Button type="button" variant="outline" onClick={props.onRetryConnections}>
              {t("retry")}
            </Button>
          </div>
        )}
        {props.connectionPages > 1 && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={props.connectionPage <= 1 || props.connectionsLoading}
              onClick={() => props.onConnectionPage(props.connectionPage - 1)}
            >
              {t("newer")}
            </Button>
            <span>
              {t("connectionPage", { page: props.connectionPage, pages: props.connectionPages })}
            </span>
            <Button
              type="button"
              variant="outline"
              disabled={props.connectionPage >= props.connectionPages || props.connectionsLoading}
              onClick={() => props.onConnectionPage(props.connectionPage + 1)}
            >
              {t("older")}
            </Button>
          </div>
        )}
      </div>
      <details className="rounded-md border p-3">
        <summary className="cursor-pointer text-sm font-medium">{t("connect")}</summary>
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <div className="space-y-2">
            <Label htmlFor={`${id}-name`}>{t("connectionName")}</Label>
            <Input
              id={`${id}-name`}
              value={name}
              maxLength={128}
              required
              disabled={connecting || props.allowed !== true}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${id}-token`}>{t("token")}</Label>
            <Input
              id={`${id}-token`}
              type="password"
              autoComplete="off"
              value={token}
              maxLength={1027}
              required
              disabled={connecting || props.allowed !== true}
              onChange={(event) => setToken(event.target.value)}
            />
            <p className="text-muted-foreground text-sm">{t("tokenHelp")}</p>
            <a
              className="text-primary text-sm underline"
              href="https://huggingface.co/settings/tokens"
              target="_blank"
              rel="noreferrer"
            >
              {t("createToken")}
            </a>
          </div>
          <Button
            type="submit"
            disabled={connecting || props.allowed !== true || !name.trim() || !token.trim()}
          >
            {t(connecting ? "connecting" : "connect")}
          </Button>
          {feedback && <p role="status">{feedback}</p>}
          {warning && <p role="alert">{warning}</p>}
        </form>
      </details>
      <details className="rounded-md border p-3">
        <summary className="cursor-pointer text-sm font-medium">{t("manualTitle")}</summary>
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            const repoId = repo.trim(),
              revisionSha = revision.trim().toLowerCase();
            if (
              !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}(\/[A-Za-z0-9][A-Za-z0-9_.-]{0,95})?$/.test(
                repoId
              ) ||
              !/^[a-f0-9]{40}$/.test(revisionSha)
            ) {
              setInvalidSource(true);
              return;
            }
            setInvalidSource(false);
            props.onManualSource({ repoId, revisionSha });
          }}
        >
          <div className="space-y-2">
            <Label htmlFor={`${id}-repo`}>{t("repository")}</Label>
            <Input
              id={`${id}-repo`}
              value={repo}
              maxLength={193}
              disabled={props.allowed !== true}
              required
              onChange={(event) => setRepo(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${id}-revision`}>{t("revision")}</Label>
            <Input
              id={`${id}-revision`}
              value={revision}
              maxLength={40}
              disabled={props.allowed !== true}
              required
              onChange={(event) => setRevision(event.target.value)}
            />
          </div>
          {invalidSource && <p role="alert">{t("invalidSource")}</p>}
          <Button type="submit" disabled={props.allowed !== true}>
            {t("useSource")}
          </Button>
        </form>
      </details>
    </section>
  );
}
