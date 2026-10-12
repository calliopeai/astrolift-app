"use client";

import { GrantAccessFlow } from "@/components/access/GrantAccessFlow";
import { useGrantPage } from "@/components/screens/administration/access/use-grant-page";

export function GrantClient() {
  const { draftKey, ...page } = useGrantPage();
  return <GrantAccessFlow key={draftKey} {...page} />;
}
