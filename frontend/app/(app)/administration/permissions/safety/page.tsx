import { PermissionsClient } from "../permissions-client";
import { ActionSafetyContent } from "./action-safety-client";

export const metadata = { title: "Action safety · Permissions · Astrolift" };

export default function ActionSafetyPage() {
  return (
    <PermissionsClient page="safety">
      <ActionSafetyContent />
    </PermissionsClient>
  );
}
