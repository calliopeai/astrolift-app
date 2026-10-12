"use client";

import { CheckCircle2Icon, CircleAlertIcon, CircleDashedIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import type { PlatformSecretStoresProps } from "./use-platform-secret-stores";

/**
 * Each organization's agent secret store, and one organization's refs with
 * whether the store holds a value. Read-only: values are set in the
 * organization's own agent secrets (#1861).
 */
export function PlatformSecretStores({
  stores,
  storesLoading,
  storesError,
  inventory,
  inventoryLoading,
  inventoryError,
  inspect,
}: PlatformSecretStoresProps) {
  const t = useTranslations("administration.platformSettings.secrets");
  const selected = inventory?.store.organizationSlug;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <p className="text-muted-foreground text-sm">{t("description")}</p>
      <Card>
        <CardContent className="flex min-w-0 flex-col">
          {storesLoading && stores.length === 0 ? (
            <Skeleton className="my-3 h-10 w-full" />
          ) : storesError ? (
            <p className="text-danger py-3 text-sm">{storesError.message}</p>
          ) : stores.length === 0 ? (
            <p className="text-muted-foreground py-6 text-center text-sm">{t("empty")}</p>
          ) : (
            stores.map((store) => (
              <div
                key={store.organizationSlug}
                className="border-border/60 flex min-w-0 flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium [overflow-wrap:anywhere]">
                    {store.organizationName}
                  </div>
                  <div className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
                    {store.clusterSlug || t("noCluster")}
                  </div>
                </div>
                {store.sharedCluster && <Badge variant="outline">{t("shared")}</Badge>}
                <span className="text-muted-foreground text-sm">
                  {t("specs", { count: store.specCount })}
                </span>
                <Button
                  size="sm"
                  variant={selected === store.organizationSlug ? "secondary" : "outline"}
                  disabled={!store.clusterSlug || store.specCount === 0 || inventoryLoading}
                  onClick={() => inspect(store.organizationSlug)}
                >
                  {t("check")}
                </Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {(inventoryLoading || inventoryError || inventory) && (
        <Card>
          <CardContent className="flex min-w-0 flex-col">
            {inventoryLoading ? (
              <Skeleton className="my-3 h-16 w-full" />
            ) : inventoryError ? (
              <p className="text-danger py-3 text-sm">{inventoryError.message}</p>
            ) : inventory && inventory.refs.length === 0 ? (
              <p className="text-muted-foreground py-6 text-center text-sm">{t("noRefs")}</p>
            ) : (
              <>
                <p className="text-muted-foreground py-2 text-sm">
                  {t("provider", { provider: inventory?.provider ?? "" })}
                </p>
                {inventory?.refs.map((ref) => (
                  <div
                    key={`${ref.specSlug}:${ref.envVar}`}
                    className="border-border/60 flex min-w-0 items-start gap-3 border-b py-2 last:border-b-0"
                  >
                    {ref.exists ? (
                      <CheckCircle2Icon className="text-success mt-0.5 size-4 shrink-0" />
                    ) : ref.error ? (
                      <CircleAlertIcon className="text-danger mt-0.5 size-4 shrink-0" />
                    ) : (
                      <CircleDashedIcon className="text-warning mt-0.5 size-4 shrink-0" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 flex-wrap gap-2 font-mono text-sm">
                        <span>{ref.envVar}</span>
                        <span className="text-muted-foreground">{ref.specSlug}</span>
                      </div>
                      <div className="text-muted-foreground font-mono text-xs [overflow-wrap:anywhere]">
                        {ref.uri}
                      </div>
                      <div className="text-sm">
                        {ref.exists ? t("present") : ref.error || t("missing")}
                      </div>
                    </div>
                  </div>
                ))}
              </>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
