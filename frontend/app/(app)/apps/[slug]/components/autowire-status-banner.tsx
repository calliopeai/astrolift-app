"use client";

import { AutowireStatusBannerView } from "@/components/screens/apps/overview/AutowireStatusBanner";
import { useAutowireRetry } from "@/components/screens/apps/overview/use-autowire-retry";
import type { AstroliftAppAutowireStatus } from "@/graphql/registry/registry.types";

interface Props {
  appSlug: string;
  sourceKind: string;
  sourceRepo: string;
  autowire?: AstroliftAppAutowireStatus | null;
}

/** Autowire completeness banner (#1108) wired to one app. */
export function AutowireStatusBanner({ appSlug, ...rest }: Props) {
  return <AutowireStatusBannerView {...rest} {...useAutowireRetry(appSlug)} />;
}
