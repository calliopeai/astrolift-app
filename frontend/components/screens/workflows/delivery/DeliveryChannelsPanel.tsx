"use client";

import * as React from "react";
import { useTranslations } from "next-intl";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export const DELIVERY_KINDS = ["slack_webhook", "slack_app", "teams_webhook", "email"] as const;
export type DeliveryKind = (typeof DELIVERY_KINDS)[number];

export interface DeliveryChannel {
  guid: string;
  name: string;
  kind: string;
  slackChannel: string;
  emailRecipients: string[];
  isActive: boolean;
  hasCredential: boolean;
}

export interface NewDeliveryChannel {
  name: string;
  kind: DeliveryKind;
  credential?: string;
  slackChannel?: string;
  emailRecipients?: string[];
}

export interface DeliveryChannelsPanelProps {
  channels: DeliveryChannel[];
  loading?: boolean;
  error?: string | null;
  /** Resolves to an error message, or null when the channel was added. */
  onCreate: (input: NewDeliveryChannel) => Promise<string | null>;
  onToggle: (guid: string, active: boolean) => Promise<unknown>;
  onRemove: (guid: string) => Promise<unknown>;
}

const KIND_KEYS: Record<DeliveryKind, string> = {
  slack_webhook: "kindSlackWebhook",
  slack_app: "kindSlackApp",
  teams_webhook: "kindTeamsWebhook",
  email: "kindEmail",
};

function kindLabel(t: ReturnType<typeof useTranslations>, kind: string) {
  return kind in KIND_KEYS ? t(KIND_KEYS[kind as DeliveryKind]) : kind;
}

/** Where a channel posts, without its credential (which is never read back). */
export function channelTarget(channel: DeliveryChannel): string {
  if (channel.kind === "email") return channel.emailRecipients.join(", ");
  if (channel.kind === "slack_app") return channel.slackChannel;
  return "";
}

function AddChannelForm({ onCreate }: { onCreate: DeliveryChannelsPanelProps["onCreate"] }) {
  const t = useTranslations("workflowDelivery");
  const [kind, setKind] = React.useState<DeliveryKind>("slack_webhook");
  const [name, setName] = React.useState("");
  const [credential, setCredential] = React.useState("");
  const [slackChannel, setSlackChannel] = React.useState("");
  const [recipients, setRecipients] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const isEmail = kind === "email";
  const ready =
    name.trim() !== "" &&
    (isEmail ? recipients.trim() !== "" : credential.trim() !== "") &&
    (kind !== "slack_app" || slackChannel.trim() !== "");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    const failure = await onCreate({
      name: name.trim(),
      kind,
      ...(isEmail
        ? {
            emailRecipients: recipients
              .split(/[\s,;]+/)
              .map((r) => r.trim())
              .filter(Boolean),
          }
        : { credential: credential.trim() }),
      ...(kind === "slack_app" ? { slackChannel: slackChannel.trim() } : {}),
    });
    setBusy(false);
    if (failure) {
      setError(failure);
      return;
    }
    setName("");
    setCredential("");
    setSlackChannel("");
    setRecipients("");
  };

  return (
    <form onSubmit={submit} className="flex max-w-lg flex-col gap-3 rounded-md border p-4">
      <h3 className="text-sm font-semibold">{t("addTitle")}</h3>
      <div className="grid gap-1.5">
        <Label htmlFor="delivery-kind">{t("kind")}</Label>
        <select
          id="delivery-kind"
          className="border-input bg-background w-full rounded-md border px-3 py-2 text-sm"
          value={kind}
          disabled={busy}
          onChange={(e) => setKind(e.target.value as DeliveryKind)}
        >
          {DELIVERY_KINDS.map((k) => (
            <option key={k} value={k}>
              {kindLabel(t, k)}
            </option>
          ))}
        </select>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="delivery-name">{t("name")}</Label>
        <Input
          id="delivery-name"
          value={name}
          disabled={busy}
          maxLength={120}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      {isEmail ? (
        <div className="grid gap-1.5">
          <Label htmlFor="delivery-recipients">{t("recipients")}</Label>
          <Textarea
            id="delivery-recipients"
            rows={2}
            value={recipients}
            disabled={busy}
            onChange={(e) => setRecipients(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">{t("recipientsHelp")}</p>
        </div>
      ) : (
        <div className="grid gap-1.5">
          <Label htmlFor="delivery-credential">
            {kind === "slack_app" ? t("botToken") : t("webhookUrl")}
          </Label>
          <Input
            id="delivery-credential"
            type="password"
            autoComplete="off"
            value={credential}
            disabled={busy}
            onChange={(e) => setCredential(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            {kind === "slack_app"
              ? t("botTokenHelp")
              : kind === "teams_webhook"
                ? t("teamsHelp")
                : t("slackHelp")}
          </p>
        </div>
      )}
      {kind === "slack_app" && (
        <div className="grid gap-1.5">
          <Label htmlFor="delivery-slack-channel">{t("slackChannel")}</Label>
          <Input
            id="delivery-slack-channel"
            value={slackChannel}
            disabled={busy}
            placeholder="C0123456789"
            onChange={(e) => setSlackChannel(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">{t("slackChannelHelp")}</p>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {error && (
          <p role="alert" className="text-danger mr-auto text-sm [overflow-wrap:anywhere]">
            {error}
          </p>
        )}
        <Button type="submit" size="sm" disabled={!ready || busy}>
          {busy ? t("adding") : t("add")}
        </Button>
      </div>
    </form>
  );
}

/**
 * Settings > Notifications > Workflow delivery (#2244): the org's channels
 * for scheduled workflow results, for org admins. A credential is entered
 * once and never shown again. Workflow editors pick channels per workflow
 * in its Settings tab. Pure: the route's hook holds the queries.
 */
export function DeliveryChannelsPanel({
  channels,
  loading = false,
  error,
  onCreate,
  onToggle,
  onRemove,
}: DeliveryChannelsPanelProps) {
  const t = useTranslations("workflowDelivery");
  const [removing, setRemoving] = React.useState<DeliveryChannel | null>(null);

  return (
    <section aria-label={t("channelsTitle")} className="flex min-w-0 flex-col gap-4">
      <div>
        <h2 className="font-semibold">{t("channelsTitle")}</h2>
        <p className="text-muted-foreground max-w-prose text-sm">{t("channelsIntro")}</p>
      </div>
      {loading ? (
        <p role="status" className="text-muted-foreground text-sm">
          {t("loading")}
        </p>
      ) : error ? (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      ) : channels.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("noChannels")}</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {channels.map((c) => (
            <li key={c.guid} className="flex min-w-0 flex-wrap items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium [overflow-wrap:anywhere]">{c.name}</p>
                <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
                  {kindLabel(t, c.kind)}
                  {channelTarget(c) ? ` · ${channelTarget(c)}` : ""}
                </p>
              </div>
              {!c.hasCredential && <Badge variant="destructive">{t("missingCredential")}</Badge>}
              <Badge variant={c.isActive ? "secondary" : "outline"}>
                {c.isActive ? t("active") : t("paused")}
              </Badge>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void onToggle(c.guid, !c.isActive)}
              >
                {c.isActive ? t("pause") : t("resume")}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setRemoving(c)}>
                {t("remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <AddChannelForm onCreate={onCreate} />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t("removeTitle", { name: removing?.name ?? "" })}
        description={t("removeDescription")}
        confirmLabel={t("remove")}
        destructive
        onConfirm={() => (removing ? onRemove(removing.guid) : undefined)}
      />
    </section>
  );
}
