"use client";

import { useTranslations } from "next-intl";
import * as React from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useFormatters } from "@/lib/i18n/formatters";

export type DelegationConnection = {
  id: string;
  version: number;
  name: string;
  state: string;
  dnsWritesSupported: boolean;
  delegationZoneName: string;
};
export type DelegationZone = { id: string; name: string };
/** The last write the provisioning workflow recorded on dns_config.delegation_write. */
export type DelegationWrite = {
  action?: string;
  reason?: string;
  nameservers?: string[];
  at?: string;
};
export type DnsDelegationPanelProps = {
  zone: string;
  boundConnectionId: string | null;
  boundZoneName: string;
  lastWrite: DelegationWrite | null;
  /** Platform operator with provider configure permission; reads stay open. */
  canWrite: boolean;
  busy: boolean;
  message: string | null;
  error: string | null;
  connections: DelegationConnection[] | null;
  connectionsLoading: boolean;
  selectedConnectionId: string | null;
  onSelectConnection: (id: string) => void;
  /** Zones in the selected connection's account that are parents of `zone`, closest first. */
  parentZones: DelegationZone[] | null;
  zonesLoading: boolean;
  zonesError: string | null;
  onGrant: (zone: DelegationZone, token: string) => Promise<boolean>;
  onBind: (connection: DelegationConnection) => Promise<boolean>;
};

export const isParentZone = (parent: string, child: string) =>
  !!parent && child.toLowerCase().endsWith("." + parent.toLowerCase());

const reasonKeys: Record<string, string> = {
  TOKEN_INACTIVE: "reasonTokenInactive",
  INVALID_TOKEN: "reasonTokenInactive",
  NOT_FOUND: "reasonZoneUnreadable",
  ZONE_CHANGED: "reasonZoneUnreadable",
  WRITE_NOT_GRANTED: "reasonNotGranted",
  NOT_A_PARENT_ZONE: "reasonNotParent",
  DOMAIN_CHANGED: "reasonChanged",
  VERSION_MISMATCH: "reasonChanged",
  CONNECTION_CHANGED: "reasonChanged",
  PERMISSION_DENIED: "reasonPermission",
  STEP_UP_REQUIRED: "reasonStepUp",
  RATE_LIMITED: "reasonRateLimited",
};

/**
 * Parent-zone delegation for one managed domain (#1787): which Cloudflare
 * connection may write the delegating NS set, the last write, and the grant
 * and bind actions. Pure (Storybook first): state comes from useDnsDelegation.
 */
export function DnsDelegationPanel(props: DnsDelegationPanelProps) {
  const t = useTranslations("domainDelegation");
  const fmt = useFormatters();
  const id = React.useId();
  const [parent, setParent] = React.useState<string | null>(null);
  const [token, setToken] = React.useState("");
  const explain = (value: string) => (reasonKeys[value] ? t(reasonKeys[value]) : value);
  const active = (props.connections ?? []).filter((c) => c.state === "ACTIVE");
  const covering = active.filter(
    (c) => c.dnsWritesSupported && isParentZone(c.delegationZoneName, props.zone)
  );
  const bound = props.connections?.find((c) => c.id === props.boundConnectionId) ?? null;
  const chosen = props.parentZones?.find((z) => z.id === parent) ?? props.parentZones?.[0] ?? null;
  const write = props.lastWrite?.action ? props.lastWrite : null;
  return (
    <section className="min-w-0 space-y-4 rounded-lg border p-4" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="font-semibold">
        {t("title")}
      </h2>
      <p className="text-muted-foreground text-sm">{t("help")}</p>
      {props.message && <p role="status">{props.message}</p>}
      {props.error && <p role="alert">{explain(props.error)}</p>}
      <dl className="grid gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">{t("binding")}</dt>
          <dd className="break-all">
            {props.boundZoneName
              ? t("boundTo", {
                  parent: props.boundZoneName,
                  connection: bound?.name ?? t("connection"),
                })
              : t("unbound")}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{t("lastWrite")}</dt>
          <dd className="space-y-1">
            {write ? (
              <>
                <Badge
                  variant={
                    write.action === "created" || write.action === "in_place"
                      ? "outline"
                      : "destructive"
                  }
                >
                  {t.has(`action.${write.action}`) ? t(`action.${write.action}`) : write.action}
                </Badge>
                {write.reason && <p className="break-all">{explain(write.reason)}</p>}
                {write.nameservers?.map((ns) => (
                  <code key={ns} className="block text-xs break-all">
                    {ns}
                  </code>
                ))}
                {write.at && (
                  <p className="text-muted-foreground text-xs">{fmt.formatDateTime(write.at)}</p>
                )}
              </>
            ) : (
              t("noWrite")
            )}
          </dd>
        </div>
      </dl>
      {props.connectionsLoading ? (
        <p role="status">{t("loading")}</p>
      ) : !props.canWrite ? (
        <p className="text-muted-foreground text-sm">{t("operatorOnly")}</p>
      ) : (
        <>
          <div className="space-y-2">
            <h3 className="text-sm font-medium">{t("detected")}</h3>
            {covering.length ? (
              <ul className="divide-y rounded-lg border">
                {covering.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                    <div className="min-w-0">
                      <p className="font-medium break-all">{c.name}</p>
                      <p className="text-muted-foreground text-xs break-all">
                        {t("grantFor", { parent: c.delegationZoneName })}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      disabled={props.busy || c.id === props.boundConnectionId}
                      onClick={() => void props.onBind(c)}
                    >
                      {c.id === props.boundConnectionId ? t("bound") : t("bind")}
                    </Button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground text-sm">{t("noneDetected")}</p>
            )}
          </div>
          <form
            className="space-y-3 rounded-lg border p-4"
            onSubmit={(event) => {
              event.preventDefault();
              const secret = token;
              setToken("");
              if (chosen && secret) void props.onGrant(chosen, secret);
            }}
          >
            <h3 className="text-sm font-medium">{t("grantTitle")}</h3>
            <p className="text-muted-foreground text-sm">{t("grantHelp")}</p>
            <div className="space-y-1">
              <Label htmlFor={`${id}-connection`}>{t("connection")}</Label>
              <select
                id={`${id}-connection`}
                className="bg-background h-8 w-full rounded-md border px-2 text-sm"
                value={props.selectedConnectionId ?? ""}
                disabled={props.busy}
                onChange={(event) => {
                  setParent(null);
                  props.onSelectConnection(event.target.value);
                }}
              >
                <option value="">{t("chooseConnection")}</option>
                {active.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              {!active.length && (
                <p className="text-muted-foreground text-sm">{t("noConnections")}</p>
              )}
            </div>
            {props.selectedConnectionId &&
              (props.zonesLoading ? (
                <p role="status">{t("loading")}</p>
              ) : props.zonesError ? (
                <p role="alert">{explain(props.zonesError)}</p>
              ) : props.parentZones?.length ? (
                <fieldset className="space-y-1">
                  <legend className="text-sm">{t("parentZone")}</legend>
                  {props.parentZones.map((z) => (
                    <label key={z.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name={`${id}-parent`}
                        checked={chosen?.id === z.id}
                        disabled={props.busy}
                        onChange={() => setParent(z.id)}
                      />
                      <span className="break-all">{z.name}</span>
                    </label>
                  ))}
                </fieldset>
              ) : (
                <p className="text-muted-foreground text-sm">
                  {t("noParent", { zone: props.zone })}
                </p>
              ))}
            <div className="space-y-1">
              <Label htmlFor={`${id}-token`}>{t("token")}</Label>
              <Input
                id={`${id}-token`}
                type="password"
                autoComplete="off"
                value={token}
                disabled={props.busy || !chosen}
                onChange={(event) => setToken(event.target.value)}
              />
              <p className="text-muted-foreground text-sm">{t("tokenHelp")}</p>
            </div>
            <Button type="submit" disabled={props.busy || !chosen || !token}>
              {t("grant")}
            </Button>
          </form>
        </>
      )}
    </section>
  );
}
