"use client";

import { AdministrationSubnav as AdministrationSubnavView } from "@/components/screens/administration/organization/AdministrationSubnav";
import { useAdministrationSubnav } from "@/components/screens/administration/organization/use-administration-subnav";

export function AdministrationSubnav() {
  return <AdministrationSubnavView {...useAdministrationSubnav()} />;
}
