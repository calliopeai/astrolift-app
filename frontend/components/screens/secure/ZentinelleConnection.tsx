"use client";

import { LinkIcon, PlugZapIcon, UnplugIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import type { ZentinelleConnection } from "./zentinelle-model";

/** A write's outcome: null when it worked, else the reason to show. */
export type WriteResult = Promise<string | null>;

export interface ConnectInput {
  url: string;
  enrollmentCode: string;
  gatewayUrl: string | null;
}

export interface ZentinelleConnectFormProps {
  connecting: boolean;
  onConnect: (input: ConnectInput) => WriteResult;
}

/**
 * Connect the organization: the Zentinelle URL plus a one-time enrollment
 * code generated in Zentinelle. The credential Zentinelle returns is stored
 * as a Secret and never shown.
 */
export function ZentinelleConnectForm({ connecting, onConnect }: ZentinelleConnectFormProps) {
  const t = useTranslations("zentinelleScreen.connect");
  const [error, setError] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const url = String(form.get("url") ?? "").trim();
    const enrollmentCode = String(form.get("enrollmentCode") ?? "").trim();
    const gatewayUrl = String(form.get("gatewayUrl") ?? "").trim();
    if (!url || !enrollmentCode) {
      setError(t("required"));
      return;
    }
    setError(await onConnect({ url, enrollmentCode, gatewayUrl: gatewayUrl || null }));
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-start gap-3">
          <PlugZapIcon className="text-primary mt-0.5 size-5 shrink-0" />
          <div>
            <p className="font-medium">{t("title")}</p>
            <p className="text-muted-foreground mt-1 max-w-2xl text-sm">{t("description")}</p>
          </div>
        </div>
        <form className="grid max-w-xl gap-3" onSubmit={submit} aria-busy={connecting}>
          <div className="grid gap-1.5">
            <Label htmlFor="zentinelle-url">{t("url")}</Label>
            <Input
              id="zentinelle-url"
              name="url"
              type="url"
              placeholder="https://zentinelle.example.com"
              disabled={connecting}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="zentinelle-code">{t("code")}</Label>
            <Input
              id="zentinelle-code"
              name="enrollmentCode"
              autoComplete="off"
              disabled={connecting}
            />
            <p className="text-muted-foreground text-xs">{t("codeHelp")}</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="zentinelle-gateway-url">{t("gatewayUrl")}</Label>
            <Input
              id="zentinelle-gateway-url"
              name="gatewayUrl"
              type="url"
              placeholder="http://zentinelle.zentinelle.svc:8000"
              disabled={connecting}
            />
            <p className="text-muted-foreground text-xs">{t("gatewayUrlHelp")}</p>
          </div>
          {error && (
            <p role="alert" className="text-danger-fg text-sm">
              {error}
            </p>
          )}
          <Button type="submit" className="w-fit" disabled={connecting}>
            {connecting ? t("connecting") : t("submit")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

export interface ZentinelleConnectionCardProps {
  connection: ZentinelleConnection;
  /** zentinelle.connect: change the gateway URL and disconnect. */
  canConnect: boolean;
  busy: boolean;
  onSetGatewayUrl: (gatewayUrl: string | null) => WriteResult;
  onDisconnect: (force: boolean) => WriteResult;
}

/** The organization's connection: where it points and the admin's actions on it. */
export function ZentinelleConnectionCard({
  connection,
  canConnect,
  busy,
  onSetGatewayUrl,
  onDisconnect,
}: ZentinelleConnectionCardProps) {
  const t = useTranslations("zentinelleScreen.connection");
  const format = useFormatter();
  const [editing, setEditing] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [confirming, setConfirming] = React.useState(false);
  const connected = connection.status === "connected";

  async function saveGatewayUrl(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = String(new FormData(event.currentTarget).get("gatewayUrl") ?? "").trim();
    const failure = await onSetGatewayUrl(value || null);
    setError(failure);
    if (!failure) setEditing(false);
  }

  const items = [
    {
      term: t("status"),
      description: (
        <span className="inline-flex items-center gap-2">
          <StatusDot status={connected ? "ok" : "error"} />
          {connected ? t("connected") : t("revoked")}
        </span>
      ),
    },
    {
      term: t("url"),
      description: <span className="font-mono text-xs">{connection.baseUrl}</span>,
    },
    {
      term: t("gatewayUrl"),
      description: (
        <span className="font-mono text-xs">{connection.gatewayUrl || t("sameAsUrl")}</span>
      ),
    },
    {
      term: t("installId"),
      description: <span className="font-mono text-xs">{connection.zentinelleInstallId}</span>,
    },
    {
      term: t("tenants"),
      description: connection.tenantIds.length ? connection.tenantIds.join(", ") : t("none"),
    },
    {
      term: t("connectedAt"),
      description: connection.connectedAt
        ? format.dateTime(new Date(connection.connectedAt), {
            dateStyle: "medium",
            timeStyle: "short",
          })
        : t("none"),
    },
  ];

  return (
    <Card>
      <CardContent className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 font-medium">
            <LinkIcon className="text-muted-foreground size-4" />
            {t("title")}
          </div>
          {canConnect && (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={busy || editing}
                onClick={() => setEditing(true)}
              >
                {t("editGatewayUrl")}
              </Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                onClick={() => setConfirming(true)}
              >
                <UnplugIcon className="mr-1 size-4" />
                {t("disconnect")}
              </Button>
            </div>
          )}
        </div>
        {!connected && connection.lastError && (
          <p role="alert" className="text-danger-fg text-sm [overflow-wrap:anywhere]">
            {t("revokedBody", { reason: connection.lastError })}
          </p>
        )}
        <DefinitionList items={items} />
        {editing && (
          <form className="flex max-w-xl flex-wrap items-end gap-2" onSubmit={saveGatewayUrl}>
            <div className="grid min-w-64 flex-1 gap-1.5">
              <Label htmlFor="zentinelle-gateway-url-edit">{t("gatewayUrl")}</Label>
              <Input
                id="zentinelle-gateway-url-edit"
                name="gatewayUrl"
                type="url"
                defaultValue={connection.gatewayUrl}
                disabled={busy}
              />
            </div>
            <Button type="submit" size="sm" disabled={busy}>
              {t("save")}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
              {t("cancel")}
            </Button>
            <p className="text-muted-foreground w-full text-xs">{t("gatewayUrlHelp")}</p>
          </form>
        )}
        {error && (
          <p role="alert" className="text-danger-fg text-sm">
            {error}
          </p>
        )}
        <ConfirmDialog
          open={confirming}
          onOpenChange={setConfirming}
          title={t("disconnectTitle")}
          description={t("disconnectBody")}
          confirmLabel={t("disconnect")}
          destructive
          onConfirm={async () => {
            const failure = await onDisconnect(!connected);
            setError(failure);
          }}
        />
      </CardContent>
    </Card>
  );
}
