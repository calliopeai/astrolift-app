"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

/** Local visible-row review, not a server incarnation/version precondition. */
export function useUnregisterReview(targets: AstroliftTenantCluster[], sourceKey: string) {
  const t = useTranslations("clusters.unregister");
  const permissions = useMyPermissions();
  const allowed =
    (permissions.loading && permissions.granted.size === 0) ||
    permissions.can("cluster.unregister");
  const key = JSON.stringify([
    sourceKey,
    allowed,
    targets.map((c) => [
      c.id,
      c.slug,
      c.name,
      c.organizationSlug,
      c.providerPluginSlug,
      c.region,
      c.lifecycle,
      c.isActive,
    ]),
  ]);
  const [target, setTarget] = React.useState<AstroliftTenantCluster | null>(null);
  const [observed, setObserved] = React.useState(key);
  if (observed !== key) {
    setObserved(key);
    setTarget(null);
  }
  const lease = React.useMemo(() => ({ key }), [key]);
  const current = React.useRef<typeof lease | null>(lease);
  React.useLayoutEffect(() => {
    current.current = lease;
    return () => {
      current.current = null;
    };
  }, [lease]);
  return {
    key,
    target,
    open: (c: AstroliftTenantCluster) => {
      if (
        current.current === lease &&
        allowed &&
        targets.some((row) => row.id === c.id && row.slug === c.slug)
      )
        setTarget({ ...c });
    },
    onOpenChange: (open: boolean) => {
      if (!open) setTarget((active) => (active === target ? null : active));
    },
    confirm: async (onUnregister: (c: AstroliftTenantCluster) => Promise<void>) => {
      if (current.current !== lease || !target || !allowed) {
        toast.error(t("sourceChanged"));
        return false;
      }
      await onUnregister(target);
    },
  };
}
