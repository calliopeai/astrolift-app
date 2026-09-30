"use client";

import { PendingDeploymentsView } from "@/components/screens/apps/controls/PendingDeployments";
import { usePendingDeployments } from "@/components/screens/apps/controls/use-pending-deployments";
import { AppDeploymentsScreen } from "@/components/screens/apps/deployments/AppDeploymentsScreen";
import { CompareDeploymentsSheetView } from "@/components/screens/apps/deployments/CompareDeploymentsSheet";
import {
  DeploymentActionDialog,
  DeploymentRowMenuItems,
} from "@/components/screens/apps/deployments/DeploymentRowActions";
import { useAppDeployments } from "@/components/screens/apps/deployments/use-app-deployments";
import { useDeploymentActions } from "@/components/screens/apps/deployments/use-deployment-actions";
import { useDeploymentComparison } from "@/components/screens/apps/deployments/use-deployment-comparison";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { appPath, useAppChrome } from "@/lib/app-chrome-context";

/**
 * The app's Deployments tab. The screen owns the markup; the approval
 * queue and the compare sheet get containers so their queries run only
 * while they are shown. Under the agent shell there is no previews route,
 * so the list drops that view.
 */
export function AppDeploymentsClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const deployments = useAppDeployments(slug, { previews: chrome.basePath === "/apps" });
  const actions = useDeploymentActions();

  return (
    <AppDeploymentsScreen
      {...deployments}
      environmentsHref={appPath(chrome, slug, "environments")}
      approvals={<Approvals appSlug={slug} />}
      renderRowActions={(d, request) => (
        <DeploymentRowMenuItems deployment={d} actions={actions} onRequest={request} />
      )}
      renderActionDialog={(target, onClose) => (
        <DeploymentActionDialog
          target={target}
          appSlug={slug}
          onClose={onClose}
          actions={actions}
        />
      )}
      renderCompare={(args) => <CompareSheet {...args} />}
    />
  );
}

function Approvals({ appSlug }: { appSlug: string }) {
  return <PendingDeploymentsView {...usePendingDeployments(appSlug)} onlyForApprovers />;
}

function CompareSheet(props: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  deployA: AstroliftDeployment;
  deployB: AstroliftDeployment;
}) {
  return (
    <CompareDeploymentsSheetView
      {...useDeploymentComparison(props.deployA.id, props.deployB.id, props.open)}
      {...props}
    />
  );
}
