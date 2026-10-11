import { PermissionsClient } from "../permissions-client";
import { AccessPresetsContent } from "./access-presets-client";

export const metadata = { title: "Easy setup · Permissions · Astrolift" };

export default function AccessPresetsPage() {
  return (
    <PermissionsClient page="presets">
      <AccessPresetsContent />
    </PermissionsClient>
  );
}
