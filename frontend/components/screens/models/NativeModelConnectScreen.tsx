"use client";

import Link from "next/link";
import { useId, useMemo } from "react";
import { useTranslations } from "next-intl";
import { BrainCircuitIcon } from "lucide-react";
import { PageShell } from "@/components/PageShell";
import { QueryError } from "@/components/QueryError";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { DefinitionList } from "@/components/ui/definition-list";
import { ListPage } from "@/components/list/ListPage";
import { useLocalListState } from "@/components/list/use-list-state";
import type { ListDefinition } from "@/components/list/list-state";
import type {
  BedrockModelSourceFieldsFragment,
  BedrockModelSourceKind,
  ListBedrockModelSourcesQuery,
  ListNativeModelClustersQuery,
  ListModelDedicatedAppsQuery,
  RegisterBedrockModelConnectionInput,
} from "@/graphql/__generated__/operations";
import type { ModelPage } from "./ModelSubscriptionsPanel";

export type NativeModelCluster =
  ListNativeModelClustersQuery["clusterModelPlacementClustersPage"]["items"][number];
type App = ListModelDedicatedAppsQuery["clusterModelDedicatedAppsPage"]["items"][number];
export type NativeModelConnectScreenProps = {
  step: 1 | 2 | 3;
  allowed: boolean;
  supportLoading: boolean;
  supportReason: string | null;
  onRetrySupport: () => void;
  cluster: NativeModelCluster | null;
  clusters: ModelPage<NativeModelCluster>;
  onSelectCluster: (row: NativeModelCluster) => Promise<void>;
  kind: BedrockModelSourceKind;
  onKind: (value: BedrockModelSourceKind) => void;
  lookup: string;
  onLookup: (value: string) => void;
  catalogue: ListBedrockModelSourcesQuery["bedrockModelSources"] | null;
  detail: BedrockModelSourceFieldsFragment | null;
  busy: boolean;
  error: string | null;
  sent: boolean;
  registeredId: string | null;
  onLoad: () => Promise<void>;
  onInspect: (identifier: string) => Promise<void>;
  name: string;
  onName: (value: string) => void;
  subscriptions: boolean;
  onSubscriptions: (value: boolean) => void;
  mode: "SHARED" | "DEDICATED";
  onMode: (value: "SHARED" | "DEDICATED") => void;
  app: App | null;
  apps: ModelPage<App>;
  onApp: (value: App) => void;
  review: RegisterBedrockModelConnectionInput | null;
  canReview: boolean;
  onReview: () => Promise<void>;
  onRegister: () => Promise<void>;
  onBack: () => void;
};

export function NativeModelConnectScreen(props: NativeModelConnectScreenProps) {
  const t = useTranslations("models.native.connect"),
    add = useTranslations("models.native.add");
  const placement = useTranslations("models.shared.placement"),
    inventory = useTranslations("models.shared.inventory");
  const common = useTranslations("models.shared.deployments"),
    hosting = useTranslations("models.shared.hosting");
  const id = useId(),
    locked = props.busy || props.sent || !props.allowed;
  const rows = props.catalogue?.items ?? [];
  // Explicit bounded read-only discovery: no public cursor, local slicing,
  // search, sort or invented total. Every returned source remains visible.
  const catalogueList = useLocalListState(
    useMemo<ListDefinition>(
      () => ({
        id: "nativeModelCatalogue",
        searchPlaceholder: t("catalogue"),
        fields: [],
        views: [{ key: "all", label: t("catalogue"), filters: {} }],
        defaultSort: [],
        paging: "numbered",
        pageSizes: [100],
        defaultPageSize: 100,
        searchable: false,
      }),
      [t]
    )
  );
  const source = props.detail?.identity;
  return (
    <PageShell
      title={add("connectTitle")}
      description={add("connectDescription")}
      actions={
        <Button variant="outline" asChild>
          <Link href="/models">{placement("back")}</Link>
        </Button>
      }
    >
      <div className="space-y-6">
        <ol className="flex flex-wrap gap-4" aria-label={t("steps")}>
          {["placementStep", "sourceStep", "reviewStep"].map((key, index) => (
            <li
              key={key}
              aria-current={props.step === index + 1 ? "step" : undefined}
              className={props.step === index + 1 ? "font-semibold" : "text-muted-foreground"}
            >
              {index + 1}. {t(key)}
            </li>
          ))}
        </ol>
        {!props.allowed ? (
          <Section title={t("availability")}>
            <p role="status">
              {props.supportLoading ? add("checking") : (props.supportReason ?? add("unavailable"))}
            </p>
            <Button
              variant="outline"
              disabled={props.supportLoading}
              onClick={props.onRetrySupport}
            >
              {hosting("retry")}
            </Button>
          </Section>
        ) : (
          <>
            {props.error && <QueryError title={t("readFailed")} error={props.error} />}
            {props.step === 1 && (
              <Section title={t("placementStep")}>
                <Label htmlFor={`${id}-provider`}>{t("provider")}</Label>
                <select
                  id={`${id}-provider`}
                  className="rounded-md border p-2"
                  value="BEDROCK"
                  onChange={() => {
                    /* Bedrock is the sole enabled native provider in this slice. */
                  }}
                  disabled={locked}
                >
                  <option value="BEDROCK">Amazon Bedrock</option>
                </select>
                <ListPage
                  embedded
                  {...props.clusters}
                  label={placement("clusters")}
                  getRowId={(row) => row.id}
                  columns={[
                    {
                      id: "name",
                      header: common("cluster"),
                      cell: (row) => (
                        <Button
                          variant="outline"
                          disabled={locked}
                          onClick={() => void props.onSelectCluster(row)}
                          className="break-all whitespace-normal"
                        >
                          {row.name} · {row.slug}
                        </Button>
                      ),
                    },
                    {
                      id: "region",
                      header: t("region"),
                      cell: (row) => row.region ?? common("unknown"),
                    },
                  ]}
                  empty={{
                    icon: <BrainCircuitIcon />,
                    title: placement("noClusters"),
                    description: t("clustersEmpty"),
                  }}
                />
              </Section>
            )}
            {props.step >= 2 && props.cluster && (
              <p>{placement("selectedCluster", { cluster: props.cluster.name })}</p>
            )}
            {props.step === 2 && (
              <>
                <Section title={t("sourceStep")} description={t("bounded", { limit: 100 })}>
                  <div className="flex flex-wrap gap-2">
                    {(["FOUNDATION_MODEL", "INFERENCE_PROFILE"] as const).map((kind) => (
                      <Button
                        key={kind}
                        variant="outline"
                        aria-pressed={props.kind === kind}
                        disabled={locked}
                        onClick={() => props.onKind(kind)}
                      >
                        {t(kind === "FOUNDATION_MODEL" ? "foundation" : "profile")}
                      </Button>
                    ))}
                    <Button variant="outline" disabled={locked} onClick={() => void props.onLoad()}>
                      {t("load")}
                    </Button>
                  </div>
                  {props.catalogue && (
                    <>
                      <p>{t("returned", { count: rows.length })}</p>
                      {props.catalogue.partial && <p role="status">{t("partial")}</p>}
                      {props.catalogue.truncated && <p role="status">{t("truncated")}</p>}
                      {props.catalogue.reason && (
                        <p role="status" className="break-words">
                          {props.catalogue.reason}
                        </p>
                      )}
                      {rows.length > 0 ||
                      props.catalogue.state === "metadata" ||
                      props.catalogue.state === "not_found" ? (
                        <ListPage
                          embedded
                          bounded
                          list={catalogueList}
                          rows={rows}
                          loading={props.busy}
                          stale={props.busy}
                          label={t("catalogue")}
                          getRowId={(row) => row.identity.sourceArn}
                          columns={[
                            {
                              id: "model",
                              header: common("model"),
                              cell: (row) => (
                                <Button
                                  variant="outline"
                                  disabled={locked}
                                  onClick={() => void props.onInspect(row.identity.sourceId)}
                                  className="break-words whitespace-normal"
                                >
                                  {row.name}
                                </Button>
                              ),
                            },
                            {
                              id: "source",
                              header: inventory("source"),
                              cell: (row) => (
                                <code className="break-all">{row.identity.sourceId}</code>
                              ),
                            },
                          ]}
                          empty={{
                            icon: <BrainCircuitIcon />,
                            title: t("empty"),
                            description: t("exactHelp"),
                          }}
                        />
                      ) : (
                        <QueryError
                          title={t("readFailed")}
                          error={props.catalogue.reason ?? t("changed")}
                        />
                      )}
                    </>
                  )}
                  <form
                    className="space-y-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void props.onInspect(props.lookup);
                    }}
                  >
                    <Label htmlFor={`${id}-lookup`}>{t("exact")}</Label>
                    <Input
                      id={`${id}-lookup`}
                      value={props.lookup}
                      onChange={(event) => props.onLookup(event.target.value)}
                      disabled={locked}
                      maxLength={2048}
                    />
                    <p className="text-muted-foreground text-sm">{t("exactHelp")}</p>
                    <Button type="submit" disabled={locked || !props.lookup.trim()}>
                      {t("inspect")}
                    </Button>
                  </form>
                </Section>
                {props.detail && (
                  <Section title={props.detail.name} description={props.detail.reason ?? undefined}>
                    <DefinitionList
                      items={[
                        { term: t("account"), description: source!.accountId },
                        { term: t("region"), description: source!.region },
                        {
                          term: inventory("source"),
                          description: <code className="break-all">{source!.sourceArn}</code>,
                        },
                        {
                          term: t("fingerprint"),
                          description: (
                            <code className="break-all">{source!.sourceFingerprint}</code>
                          ),
                        },
                      ]}
                    />
                    <p>{t("accessUnknown")}</p>
                    <Label htmlFor={`${id}-name`}>{t("name")}</Label>
                    <Input
                      id={`${id}-name`}
                      value={props.name}
                      disabled={locked}
                      maxLength={128}
                      onChange={(event) => props.onName(event.target.value)}
                    />
                    <label className="flex gap-2">
                      <input
                        type="checkbox"
                        checked={props.subscriptions}
                        disabled={locked}
                        onChange={(event) => props.onSubscriptions(event.target.checked)}
                      />
                      {placement("allowSubscriptions")}
                    </label>
                    <div className="flex flex-wrap gap-2">
                      {(["SHARED", "DEDICATED"] as const).map((mode) => (
                        <Button
                          key={mode}
                          variant="outline"
                          aria-pressed={props.mode === mode}
                          disabled={locked}
                          onClick={() => props.onMode(mode)}
                        >
                          {inventory(mode === "SHARED" ? "shared" : "dedicated")}
                        </Button>
                      ))}
                    </div>
                    <p className="text-muted-foreground text-sm">{inventory("sharingHelp")}</p>
                    {props.mode === "DEDICATED" && (
                      <ListPage
                        embedded
                        {...props.apps}
                        label={inventory("selectApp")}
                        getRowId={(row) => row.id}
                        columns={[
                          {
                            id: "app",
                            header: inventory("selectApp"),
                            cell: (row) => (
                              <Button
                                variant="outline"
                                disabled={locked}
                                onClick={() => props.onApp(row)}
                              >
                                {row.name}
                              </Button>
                            ),
                          },
                        ]}
                        empty={{
                          icon: <BrainCircuitIcon />,
                          title: inventory("appsEmpty"),
                          description: inventory("appsEmptyHelp"),
                        }}
                      />
                    )}
                    {props.app && <p>{inventory("dedicatedApp", { app: props.app.name })}</p>}
                    <Button disabled={!props.canReview} onClick={() => void props.onReview()}>
                      {t("review")}
                    </Button>
                  </Section>
                )}
              </>
            )}
            {props.step === 3 && props.review && source && (
              <Section title={t("reviewStep")} description={t("registerHelp")}>
                <DefinitionList
                  items={[
                    { term: t("name"), description: props.review.name },
                    { term: t("account"), description: source.accountId },
                    { term: t("region"), description: source.region },
                    {
                      term: inventory("source"),
                      description: <code className="break-all">{source.sourceArn}</code>,
                    },
                    {
                      term: t("fingerprint"),
                      description: (
                        <code className="break-all">{props.review.sourceFingerprint}</code>
                      ),
                    },
                    {
                      term: inventory("access"),
                      description: inventory(
                        props.review.sharingMode === "DEDICATED" ? "dedicated" : "shared"
                      ),
                    },
                  ]}
                />
                <p>{t("accessUnknown")}</p>
                {props.app && <p>{inventory("dedicatedApp", { app: props.app.name })}</p>}
                <p>
                  {placement("allowSubscriptions")}:{" "}
                  {common(props.review.allowSubscriptions ? "enabled" : "disabled")}
                </p>
                {props.registeredId ? (
                  <div role="status">
                    <p>{t("registered")}</p>
                    <Button asChild>
                      <Link href={`/models/shared/${encodeURIComponent(props.registeredId)}`}>
                        {placement("openDeployment")}
                      </Link>
                    </Button>
                  </div>
                ) : (
                  <Button disabled={locked} onClick={() => void props.onRegister()}>
                    {t("register")}
                  </Button>
                )}
              </Section>
            )}
            {props.sent && !props.registeredId && <p role="status">{t("unconfirmed")}</p>}
            {props.step > 1 && (
              <Button variant="outline" disabled={locked} onClick={props.onBack}>
                {hosting("newer")}
              </Button>
            )}
          </>
        )}
      </div>
    </PageShell>
  );
}
