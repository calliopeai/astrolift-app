"use client";

import { BootstrapPlanView } from "@/components/screens/clusters/settings/BootstrapPlan";
import { ClusterAgentView } from "@/components/screens/clusters/settings/ClusterAgent";
import {
  BootstrapHistoryView,
  ClusterSettingsScreen,
} from "@/components/screens/clusters/settings/ClusterSettings";
import { IngressAuthView } from "@/components/screens/clusters/settings/IngressAuth";
import type { ClusterWithHeartbeat } from "@/components/screens/clusters/settings/types";
import {
  useBootstrapHistory,
  useBootstrapPlan,
} from "@/components/screens/clusters/settings/use-bootstrap";
import { useClusterAgent } from "@/components/screens/clusters/settings/use-cluster-agent";
import { useClusterSettings } from "@/components/screens/clusters/settings/use-cluster-settings";
import { useIngressAuth } from "@/components/screens/clusters/settings/use-ingress-auth";

import { ClusterTabs } from "@/components/screens/clusters/list/ClusterTabs";

import { AuthUsersCard } from "./auth-users-card";
import { CentralAuthCard, IngressClassCard } from "./central-auth-card";

/**
 * Cluster settings tab. The screen owns the markup; each card with data of
 * its own gets a container here so its hook runs only when it is rendered
 * (permission-gated cards, the history disclosure).
 */
export function ClusterSettingsClient({ slug }: { slug: string }) {
  const settings = useClusterSettings(slug);
  const cluster = settings.cluster;

  return (
    <ClusterSettingsScreen
      {...settings}
      slug={slug}
      tabs={<ClusterTabs slug={slug} active="settings" />}
      cards={
        cluster
          ? {
              agent: <ClusterAgentCard cluster={cluster} />,
              ingressClass: <IngressClassCard cluster={cluster} />,
              centralAuth: <CentralAuthCard cluster={cluster} />,
              ingressAuth: <IngressAuthCard cluster={cluster} />,
              authUsers: <AuthUsersCard clusterId={cluster.id} />,
              bootstrapPlan: <BootstrapPlanCard clusterId={cluster.id} />,
            }
          : undefined
      }
      bootstrapHistory={<BootstrapHistory slug={slug} />}
    />
  );
}

function ClusterAgentCard({ cluster }: { cluster: ClusterWithHeartbeat }) {
  return <ClusterAgentView {...useClusterAgent(cluster)} />;
}

function IngressAuthCard({ cluster }: { cluster: ClusterWithHeartbeat }) {
  return <IngressAuthView {...useIngressAuth(cluster)} />;
}

function BootstrapPlanCard({ clusterId }: { clusterId: string }) {
  return <BootstrapPlanView {...useBootstrapPlan(clusterId)} />;
}

function BootstrapHistory({ slug }: { slug: string }) {
  return <BootstrapHistoryView {...useBootstrapHistory(slug)} />;
}
