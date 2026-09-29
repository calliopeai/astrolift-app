"use client";

import { EnvironmentDetail } from "@/components/screens/environments/EnvironmentDetail";
import { useEnvironment } from "@/components/screens/environments/use-environment";

/** Environment detail (#1106), the drill-in target for an /environments row. */
export function EnvironmentDetailClient({ id }: { id: string }) {
  return <EnvironmentDetail id={id} {...useEnvironment(id)} />;
}
