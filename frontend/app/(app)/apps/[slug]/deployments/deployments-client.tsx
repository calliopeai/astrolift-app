"use client";

import { AppDeploymentsScreen } from "@/components/screens/apps/deployments/AppDeploymentsScreen";
import { CompareDeploymentsSheetView } from "@/components/screens/apps/deployments/CompareDeploymentsSheet";
import { DeploymentExpandPanelView } from "@/components/screens/apps/deployments/DeploymentExpandPanel";
import { DeploymentRowActionsView } from "@/components/screens/apps/deployments/DeploymentRowActions";
import { useAppDeployments } from "@/components/screens/apps/deployments/use-app-deployments";
import { useDeploymentActions } from "@/components/screens/apps/deployments/use-deployment-actions";
import { useDeploymentComparison } from "@/components/screens/apps/deployments/use-deployment-comparison";
import { useDeploymentDetail } from "@/components/screens/apps/deployments/use-deployment-detail";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";

/**
 * App deployments tab. The screen owns the markup; each row's actions, the
 * opened row's detail and the compare sheet get a container here so their
 * queries and mutations run only for what is rendered.
 */
export function AppDeploymentsClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const deployments = useAppDeployments(slug);
  const a = deployments.app;
  const repoFullName = a?.sourceRepo ?? "";

  return (
    <AppDeploymentsScreen
      {...deployments}
      slug={slug}
      environmentsHref={appPath(chrome, a?.slug ?? slug, "environments")}
      tabs={a ? <AppTabs slug={a.slug} active="deployments" /> : null}
      renderRowActions={(d) => <RowActions deployment={d} appSlug={a?.slug ?? slug} />}
      renderExpandPanel={(d, onClose) => (
        <ExpandPanel deployment={d} repoFullName={repoFullName} onClose={onClose} />
      )}
      renderCompare={(args) => <CompareSheet {...args} />}
    />
  );
}

function RowActions({ deployment, appSlug }: { deployment: AstroliftDeployment; appSlug: string }) {
  return (
    <DeploymentRowActionsView
      {...useDeploymentActions(deployment, appSlug)}
      deployment={deployment}
      appSlug={appSlug}
    />
  );
}

function ExpandPanel({
  deployment,
  repoFullName,
  onClose,
}: {
  deployment: AstroliftDeployment;
  repoFullName: string;
  onClose: () => void;
}) {
  return (
    <DeploymentExpandPanelView
      {...useDeploymentDetail(deployment)}
      deployment={deployment}
      repoFullName={repoFullName}
      onClose={onClose}
    />
  );
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
