"use client";

import { BundleHomeScreen } from "@/components/screens/apps/homes/BundleHome";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { appPath, useAppChrome } from "@/lib/app-chrome-context";

interface BundleHomeProps {
  slug: string;
  name: string;
  status: string;
  workloads: AstroliftWorkload[];
}

/** Bundle home: the route's base path decides where each workload card links. */
export function BundleHome({ slug, name, status, workloads }: BundleHomeProps) {
  const chrome = useAppChrome();
  return (
    <BundleHomeScreen
      name={name}
      status={status}
      workloads={workloads}
      workloadHref={(workloadSlug) =>
        appPath(chrome, slug, "workloads", encodeURIComponent(workloadSlug))
      }
    />
  );
}
