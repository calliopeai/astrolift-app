"use client";

import { ModulesCard as ModulesCardView } from "@/components/screens/administration/organization/ModulesCard";
import { useModulesCard } from "@/components/screens/administration/organization/use-modules-card";

/** The per-org modules card wired to setOrganizationModule (#1880). */
export function ModulesCard() {
  return <ModulesCardView {...useModulesCard()} />;
}
