"use client";

import { UrlCardView } from "@/components/screens/apps/overview/UrlCard";
import { type UseUrlCardArgs, useUrlCard } from "@/components/screens/apps/overview/use-url-card";

import { UrlHealthBadge } from "./url-health-badge";

/**
 * Primary URL card for the app overview (#408). The health pill is a slot
 * the view mounts only when the host is routable, so it polls only then.
 */
export function UrlCard(props: UseUrlCardArgs) {
  const card = useUrlCard(props);
  return (
    <UrlCardView
      {...card}
      healthBadge={<UrlHealthBadge appSlug={props.appSlug} url={`https://${card.fullHost}/`} />}
    />
  );
}
