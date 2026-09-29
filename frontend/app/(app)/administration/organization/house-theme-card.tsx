"use client";

import { HouseThemeCard as HouseThemeCardView } from "@/components/screens/administration/organization/HouseThemeCard";
import { useHouseTheme } from "@/components/screens/administration/organization/use-house-theme";
import type { AstroliftOrganization } from "@/graphql/identity/identity.types";

/** The house-theme card wired to the active org (#135). */
export function HouseThemeCard({ org }: { org: AstroliftOrganization }) {
  return <HouseThemeCardView {...useHouseTheme(org)} />;
}
