"use client";

import { AccessPresetsView } from "@/components/screens/administration/permissions/AccessPresetsView";
import { DeployApprovalSetup } from "@/components/screens/administration/permissions/DeployApprovalSetup";
import { useAccessPresets } from "@/components/screens/administration/permissions/use-access-presets";
import { useDeployApprovalSetup } from "@/components/screens/administration/permissions/use-deploy-approval-setup";

/** Admin › Permissions › Easy setup (#2272), mounted only while the permissions console is on. */
export function AccessPresetsContent() {
  return <AccessPresetsView {...useAccessPresets()} deployApproval={<DeployApprovalContent />} />;
}

function DeployApprovalContent() {
  return <DeployApprovalSetup {...useDeployApprovalSetup()} />;
}
