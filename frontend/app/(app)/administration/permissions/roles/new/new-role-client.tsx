"use client";

import { NewRoleScreen } from "@/components/screens/administration/permissions/NewRoleScreen";
import { useNewRole } from "@/components/screens/administration/permissions/use-new-role";

/** Admin › Permissions › Roles › New role: the stepped create page. */
export function NewRoleClient() {
  const props = useNewRole();
  // `?from=` resolves once the roles land; the screen reseeds from it then.
  return <NewRoleScreen key={props.from?.id ?? "blank"} {...props} />;
}
