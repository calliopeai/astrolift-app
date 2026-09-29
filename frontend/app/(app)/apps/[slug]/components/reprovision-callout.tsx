"use client";

import { ReprovisionCalloutView } from "@/components/screens/apps/overview/ReprovisionCallout";
import { useForceReprovision } from "@/components/screens/apps/overview/use-force-reprovision";
import type { AstroliftAppReprovisionState } from "@/graphql/registry/registry.types";

/** Reprovision callout (#407 A) wired to one app. */
export function ReprovisionCallout({
  appSlug,
  reprovision,
}: {
  appSlug: string;
  reprovision: AstroliftAppReprovisionState;
}) {
  return <ReprovisionCalloutView reprovision={reprovision} {...useForceReprovision(appSlug)} />;
}
