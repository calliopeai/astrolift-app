"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { domainDiagnosticReasonKey } from "./domain-diagnostic-reasons";
import type { DnsConnection, DnsConnectionSetupState } from "./use-dns-connection-setup";

export type DnsConnectionWizardProps = DnsConnectionSetupState & { initialStep?: Step };
type Step = "provider" | "connection" | "zone" | "review" | "verify";
const steps: Step[] = ["provider", "connection", "zone", "review", "verify"];

/** Pure setup surface: every provider read/effect is an explicit typed callback. */
export function DnsConnectionWizard(props: DnsConnectionWizardProps) {
  const t = useTranslations("domainConnections"),
    d = useTranslations("managedDomains");
  const [step, setStep] = React.useState<Step>(props.initialStep ?? "provider");
  const [name, setName] = React.useState("");
  const [token, setToken] = React.useState("");
  const [confirmed, setConfirmed] = React.useState(false);
  const [disconnect, setDisconnect] = React.useState<DnsConnection | null>(null);
  const [filter, setFilter] = React.useState("");
  const [copyMessage, setCopyMessage] = React.useState<string | null>(null);
  function explanation(value: string) {
    const key = domainDiagnosticReasonKey(value);
    return key ? d(key) : value;
  }
  const readBusy = props.busy || props.supportLoading || props.targetLoading;
  const canReview =
    props.allowed &&
    !!props.connection &&
    !!props.selectedZone &&
    props.records?.complete === true &&
    !props.recordsError &&
    !props.recordsLoading;
  const canRegister =
    canReview &&
    confirmed &&
    !readBusy &&
    !props.uncertain &&
    (!props.target || props.target.zone === props.selectedZone?.name) &&
    !props.targetError;
  const records = (props.records?.items ?? []).filter((row) =>
    [row.name, row.type, row.content].join(" ").toLowerCase().includes(filter.toLowerCase())
  );
  const chooseStep = (next: Step) => {
    setToken("");
    setConfirmed(false);
    setStep(next);
  };
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopyMessage(d("copied"));
    } catch {
      setCopyMessage(d("copyFailed"));
    }
  }
  return (
    <PageShell
      title={t("title")}
      description={t("description")}
      actions={
        <Button variant="outline" onClick={props.onRefresh} disabled={props.busy}>
          {d("refresh")}
        </Button>
      }
    >
      <Link href="/domains" className="text-sm underline">
        {d("back")}
      </Link>
      <ol aria-label={t("title")} className="flex flex-wrap gap-2 text-sm">
        {steps.map((value, index) => (
          <li
            key={value}
            aria-current={step === value ? "step" : undefined}
            className={step === value ? "font-semibold" : "text-muted-foreground"}
          >
            {index + 1}. {t(`${value}Step`)}
          </li>
        ))}
      </ol>
      {props.supportError && <p role="alert">{explanation(props.supportError)}</p>}
      {props.targetError && <p role="alert">{explanation(props.targetError)}</p>}
      {props.message && <p role="status">{props.message}</p>}
      {props.actionError && <p role="alert">{explanation(props.actionError)}</p>}
      {props.uncertain && (
        <Link className="block text-sm underline" href="/domains">
          {d("back")}
        </Link>
      )}
      {step === "verify" && (props.bindingLoading || props.targetLoading) && (
        <p role="status">{d("loading")}</p>
      )}
      {step === "verify" && props.bindingError && (
        <p role="alert">{explanation(props.bindingError)}</p>
      )}
      {step === "verify" &&
        !props.verification &&
        !props.bindingLoading &&
        !props.targetLoading && <p>{t("unavailable")}</p>}
      {copyMessage && <p role="status">{copyMessage}</p>}
      {props.supportLoading && <p role="status">{d("running")}</p>}
      {step === "provider" && (
        <section className="space-y-4" aria-label={t("providerStep")}>
          <div className="grid gap-3 md:grid-cols-2">
            <article className="space-y-3 rounded-lg border p-4">
              <h2 className="font-semibold">Cloudflare</h2>
              <p className="text-sm">{t("cloudflareHelp")}</p>
              <Button
                disabled={readBusy || !props.allowed || props.uncertain}
                onClick={() => {
                  props.onProvider("cloudflare");
                  chooseStep("connection");
                }}
              >
                Cloudflare
              </Button>
              {props.support && !props.support.allowed && (
                <p role="status">{explanation(props.support.reason) || t("unavailable")}</p>
              )}
            </article>
            <article className="space-y-3 rounded-lg border p-4">
              <h2 className="font-semibold">AWS Route53</h2>
              <p className="text-sm">{t("route53Help")}</p>
              <Button
                disabled={readBusy || props.uncertain}
                onClick={() => {
                  props.onProvider("route53");
                  chooseStep("zone");
                }}
              >
                AWS Route53
              </Button>
            </article>
          </div>
        </section>
      )}
      {props.provider === "route53" && step !== "provider" && (
        <section className="space-y-3" aria-label={t("zoneStep")}>
          <h2 className="font-semibold">{t("zoneStep")}</h2>
          <p className="text-sm">{t("route53Help")}</p>
          {props.route53Loading ? (
            <p role="status">{d("loading")}</p>
          ) : props.route53Error ? (
            <p role="alert">{explanation(props.route53Error)}</p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {props.route53Domains.map((domain) => (
                <li key={domain.id} className="space-y-1 p-3">
                  <Link className="underline" href={`/domains/${domain.id}`}>
                    {domain.zone}
                  </Link>
                  <p className="text-muted-foreground text-sm">{d("provisionHelp")}</p>
                </li>
              ))}
            </ul>
          )}
          {!props.route53Loading && !props.route53Error && !props.route53Domains.length && (
            <p>{t("route53Empty")}</p>
          )}
          <Link href="/domains" className="text-sm underline">
            {t("configureRoute53")}
          </Link>
          <Button variant="outline" disabled={props.busy} onClick={() => chooseStep("provider")}>
            {t("back")}
          </Button>
        </section>
      )}
      {props.provider === "cloudflare" && step === "connection" && (
        <section className="space-y-4" aria-label={t("connectionStep")}>
          <p className="text-sm">{t("readOnly")}</p>
          <form
            className="space-y-3 rounded-lg border p-4"
            onSubmit={(event) => {
              event.preventDefault();
              const secret = token;
              setToken("");
              void props.onConnect(name, secret);
            }}
          >
            <div className="space-y-1">
              <Label htmlFor="dns-connection-name">{t("connectionName")}</Label>
              <Input
                id="dns-connection-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={120}
                disabled={readBusy}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="dns-connection-token">{t("token")}</Label>
              <Input
                id="dns-connection-token"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                disabled={readBusy || props.support?.apiTokenSupported !== true}
              />
              <p className="text-muted-foreground text-sm">{t("tokenHelp")}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="submit"
                disabled={
                  readBusy ||
                  props.uncertain ||
                  !props.allowed ||
                  !props.support?.apiTokenSupported ||
                  !name.trim() ||
                  !token
                }
              >
                {t("saveToken")}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={
                  readBusy ||
                  props.uncertain ||
                  !props.allowed ||
                  !props.support?.oauthConfigured ||
                  !name.trim()
                }
                onClick={() => {
                  setToken("");
                  void props.onOAuth(name);
                }}
              >
                {t("oauth")}
              </Button>
            </div>
            {props.support && !props.support.oauthConfigured && (
              <p className="text-sm">
                {t("oauthUnavailable")} {props.support.oauthSetupReason}
              </p>
            )}
          </form>
          <h2 className="font-semibold">{t("connections")}</h2>
          {props.connectionsLoading ? (
            <p role="status">{d("loading")}</p>
          ) : props.connectionsError ? (
            <p role="alert">{explanation(props.connectionsError)}</p>
          ) : props.connections ? (
            <>
              <ul className="divide-y rounded-lg border">
                {props.connections.items.map((connection) => (
                  <li
                    key={`${connection.id}:${connection.version}`}
                    className="flex flex-wrap items-center justify-between gap-2 p-3"
                  >
                    <div>
                      <p className="font-medium">{connection.name}</p>
                      <p className="text-muted-foreground text-xs">
                        {connection.authMethod} · {connection.state} · v{connection.version}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        disabled={readBusy || !props.allowed || connection.state !== "ACTIVE"}
                        onClick={() => {
                          props.onConnection(connection);
                          chooseStep("zone");
                        }}
                      >
                        {t("chooseConnection")}
                      </Button>
                      <Button
                        variant="outline"
                        disabled={readBusy || !props.allowed || connection.state === "DISCONNECTED"}
                        onClick={() => void props.onRetest(connection)}
                      >
                        {t("retest")}
                      </Button>
                      <Button
                        variant="destructive"
                        disabled={readBusy || !props.allowed || connection.state === "DISCONNECTED"}
                        onClick={() => setDisconnect(connection)}
                      >
                        {t("disconnect")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
              {!props.connections.items.length && <p>{t("connectionsEmpty")}</p>}
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  disabled={readBusy || props.page <= 1}
                  onClick={() => props.onPage(props.page - 1)}
                >
                  {t("previousPage")}
                </Button>
                <span className="self-center text-sm">{props.page}</span>
                <Button
                  variant="outline"
                  disabled={
                    readBusy ||
                    props.connections.totalCount == null ||
                    props.page * 20 >= props.connections.totalCount
                  }
                  onClick={() => props.onPage(props.page + 1)}
                >
                  {t("nextPage")}
                </Button>
              </div>
            </>
          ) : (
            <p>{t("unavailable")}</p>
          )}
          <Button variant="outline" disabled={props.busy} onClick={() => chooseStep("provider")}>
            {t("back")}
          </Button>
        </section>
      )}
      {props.provider === "cloudflare" && step === "zone" && (
        <section className="space-y-3" aria-label={t("zoneStep")}>
          <h2 className="font-semibold">{t("zoneStep")}</h2>
          <p>{props.connection?.name ?? t("chooseConnection")}</p>
          {props.zonesLoading ? (
            <p role="status">{d("loading")}</p>
          ) : props.zonesError ? (
            <p role="alert">{explanation(props.zonesError)}</p>
          ) : props.zones ? (
            <>
              {!props.zones.complete && (
                <p role="alert">
                  {t("inventoryIncomplete")} {props.zones.reason}
                </p>
              )}
              <ul className="divide-y rounded-lg border">
                {props.zones.items.map((zone) => (
                  <li
                    key={zone.id}
                    className="flex flex-wrap items-center justify-between gap-2 p-3"
                  >
                    <div>
                      <p className="font-medium">{zone.name}</p>
                      <p className="text-muted-foreground text-xs">
                        {zone.status} · {zone.id}
                      </p>
                    </div>
                    <Button
                      disabled={
                        readBusy || !props.allowed || !props.zones?.complete || !props.connection
                      }
                      onClick={() => {
                        props.onZone(zone);
                        chooseStep("review");
                      }}
                    >
                      {t("selectZone")}
                    </Button>
                  </li>
                ))}
              </ul>
              {!props.zones.items.length && props.zones.complete && <p>{t("zoneEmpty")}</p>}
            </>
          ) : (
            !props.zonesLoading && <p>{t("unavailable")}</p>
          )}
          <Button variant="outline" disabled={props.busy} onClick={() => chooseStep("connection")}>
            {t("back")}
          </Button>
        </section>
      )}
      {props.provider === "cloudflare" && step === "review" && (
        <section className="space-y-4" aria-label={t("reviewStep")}>
          <h2 className="font-semibold">{props.selectedZone?.name ?? t("zoneStep")}</h2>
          <Badge variant="outline">READ_ONLY_DISCOVERY</Badge>
          <p className="text-sm">{t("noWrites")}</p>
          {props.target && (
            <p>
              {t("attachTarget")}: {props.target.zone}
            </p>
          )}
          {props.target && props.selectedZone && props.target.zone !== props.selectedZone.name && (
            <p role="alert">{t("targetUnavailable")}</p>
          )}
          <h3 className="font-medium">{d("nameservers")}</h3>
          <ul>
            {props.selectedZone?.nameServers.map((value) => (
              <li key={value} className="flex items-start gap-2">
                <code className="break-all">{value}</code>
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() => void copy(value)}
                  aria-label={`${d("copy")} ${value}`}
                >
                  {d("copy")}
                </Button>
              </li>
            ))}
          </ul>
          <p className="text-sm">{t("delegationHelp")}</p>
          <Label htmlFor="dns-record-filter">{d("recordSearch")}</Label>
          <Input
            id="dns-record-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
          {props.recordsLoading ? (
            <p role="status">{d("running")}</p>
          ) : props.recordsError ? (
            <p role="alert">{explanation(props.recordsError)}</p>
          ) : props.records ? (
            <>
              {!props.records.complete && (
                <p role="alert">
                  {t("inventoryIncomplete")} {props.records.reason}
                </p>
              )}
              <ul className="divide-y rounded-lg border">
                {records.map((record) => (
                  <li key={record.id} className="space-y-1 p-3">
                    <p className="font-medium break-all">
                      {record.name} · {record.type}
                    </p>
                    <code className="block text-xs break-all">{record.content}</code>
                    <p className="text-muted-foreground text-xs">
                      {d("ttl")}: {record.ttl} · {t("proxied")}:{" "}
                      {record.proxied ? d("yes") : d("no")}
                    </p>
                  </li>
                ))}
              </ul>
              {!records.length && <p>{d("noRecords")}</p>}
            </>
          ) : (
            <p>{t("unavailable")}</p>
          )}
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={!canReview || readBusy}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            {t("acknowledge")}
          </label>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={props.busy} onClick={() => chooseStep("zone")}>
              {t("back")}
            </Button>
            <Button
              disabled={!canRegister}
              onClick={() =>
                void props.onRegister().then((ok) => {
                  if (ok) chooseStep("verify");
                })
              }
            >
              {props.target ? t("attach") : t("register")}
            </Button>
          </div>
        </section>
      )}
      {step === "verify" && props.verification && (
        <section className="space-y-4" aria-label={t("verifyStep")}>
          <h2 className="font-semibold">{props.verification.zoneName}</h2>
          <p>{t("verificationHelp")}</p>
          <Badge variant="outline">{props.verification.verificationState}</Badge>
          <dl className="space-y-3">
            {[
              [d("challengeName"), props.verification.verificationRecordName],
              [d("challengeValue"), props.verification.verificationRecordValue],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-sm font-medium">{label}</dt>
                <dd className="flex items-start gap-2">
                  <code className="text-xs break-all">{value ?? d("unknown")}</code>
                  {value && (
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={() => void copy(value)}
                      aria-label={`${d("copy")} ${label}`}
                    >
                      {d("copy")}
                    </Button>
                  )}
                </dd>
              </div>
            ))}
          </dl>
          <Button disabled={readBusy || !props.canVerify} onClick={() => void props.onVerify()}>
            {t("verify")}
          </Button>
          <Link
            href={`/domains/${props.verification.domainId}`}
            className="block text-sm underline"
          >
            {t("openDomain")}
          </Link>
        </section>
      )}
      <ConfirmDialog
        open={disconnect !== null}
        onOpenChange={(next) => {
          if (!next) setDisconnect(null);
        }}
        title={t("disconnectTitle")}
        description={t("disconnectHelp")}
        destructive
        confirmLabel={t("disconnect")}
        confirmDisabled={readBusy || !props.allowed}
        onConfirm={() => (disconnect ? props.onDisconnect(disconnect) : false)}
      />
    </PageShell>
  );
}
