"use client";

import { usePathname, useSearchParams } from "next/navigation";

import { useBrainStatus } from "../use-brain-status";
import { environmentNames, pickEnvironment } from "./brain-detail-model";
import type { EnvironmentChoice } from "./EnvironmentPicker";
import type { FramedBrainValue } from "./use-brain-frame";

/**
 * The status the Overview and Substrate tabs read: one environment's,
 * picked by `?env=` (production by default), read again on arrival.
 */
export function useBrainEnvironmentStatus(framed: FramedBrainValue) {
  const pathname = usePathname() ?? "";
  const params = useSearchParams();
  const environment = pickEnvironment(framed.brain, params?.get("env") ?? null);
  const status = useBrainStatus(framed.brain.slug, environment, { fresh: true });
  const environments: EnvironmentChoice[] = environmentNames(framed.brain).map((name) => {
    const query = new URLSearchParams(params?.toString() ?? "");
    query.set("env", name);
    return { name, href: `${pathname}?${query.toString()}`, active: name === environment };
  });
  return { environment, environments, status: status.result, onRetry: status.onRetry };
}

/** Overview: the layer and its status in the picked environment. */
export function useBrainOverview(framed: FramedBrainValue) {
  return { brain: framed.brain, ...useBrainEnvironmentStatus(framed) };
}
