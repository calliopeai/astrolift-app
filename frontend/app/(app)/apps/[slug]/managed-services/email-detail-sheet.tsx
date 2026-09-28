"use client";

import {
  AlertRulesPanelView,
  CostPanelView,
  EmailDetailSheetView,
  EngagementMetricsPanelView,
  MessageLogPanelView,
  SenderConfigPanelView,
  SuppressionPanelView,
  TemplatesPanelView,
  TemplateStatsView,
} from "@/components/screens/apps/managed-services/EmailDetailSheet";
import {
  useEmailCost,
  useEmailDetail,
  useEmailTemplates,
  useEngagementMetrics,
  useMessageLog,
  useSenderAlertRules,
  useSenderConfig,
  useSuppressionList,
  useTemplateStats,
} from "@/components/screens/apps/managed-services/use-email-detail";
import type { AstroliftEmailServiceDetail } from "@/graphql/services/services.types";

interface EmailDetailSheetProps {
  managedServiceId: string;
  serviceName: string;
  appSlug: string;
  serviceConfig: Record<string, unknown>;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}

/**
 * Email-service detail sheet (#629 and friends). The view owns the
 * markup; each panel with data of its own gets a container here so its
 * queries run only once the detail has loaded and the panel is mounted.
 */
export function EmailDetailSheet({
  managedServiceId,
  serviceName,
  appSlug,
  serviceConfig,
  open,
  onOpenChange,
}: EmailDetailSheetProps) {
  const { detail, loading, onRefetch } = useEmailDetail(managedServiceId, open);

  return (
    <EmailDetailSheetView
      serviceName={serviceName}
      serviceConfig={serviceConfig}
      open={open}
      onOpenChange={onOpenChange}
      detail={detail}
      loading={loading}
      panels={
        detail
          ? {
              cost: (
                <CostPanel
                  managedServiceId={detail.managedServiceId}
                  appSlug={appSlug}
                  open={open}
                />
              ),
              suppression: <SuppressionPanel detail={detail} onRefetch={onRefetch} />,
              senderConfig: (
                <SenderConfigPanel
                  managedServiceId={managedServiceId}
                  serviceConfig={serviceConfig}
                />
              ),
              alertRules: <SenderAlertRulesPanel managedServiceId={managedServiceId} />,
              engagement: (
                <EngagementMetricsPanel
                  managedServiceId={managedServiceId}
                  snsConfigured={
                    typeof serviceConfig.sns_event_destination_arn === "string" &&
                    (serviceConfig.sns_event_destination_arn as string).length > 0
                  }
                />
              ),
              messageLog: <MessageLogPanel managedServiceId={managedServiceId} />,
              templates: <TemplateManagementPanel managedServiceId={managedServiceId} />,
            }
          : undefined
      }
    />
  );
}

function CostPanel({
  managedServiceId,
  appSlug,
  open,
}: {
  managedServiceId: string;
  appSlug: string;
  open: boolean;
}) {
  return <CostPanelView {...useEmailCost(managedServiceId, appSlug, open)} />;
}

function SuppressionPanel({
  detail,
  onRefetch,
}: {
  detail: AstroliftEmailServiceDetail;
  onRefetch: () => void;
}) {
  return (
    <SuppressionPanelView
      detail={detail}
      {...useSuppressionList(detail.managedServiceId, onRefetch)}
    />
  );
}

function SenderConfigPanel({
  managedServiceId,
  serviceConfig,
}: {
  managedServiceId: string;
  serviceConfig: Record<string, unknown>;
}) {
  return <SenderConfigPanelView {...useSenderConfig(managedServiceId, serviceConfig)} />;
}

function SenderAlertRulesPanel({ managedServiceId }: { managedServiceId: string }) {
  return <AlertRulesPanelView {...useSenderAlertRules(managedServiceId)} />;
}

function EngagementMetricsPanel({
  managedServiceId,
  snsConfigured,
}: {
  managedServiceId: string;
  snsConfigured: boolean;
}) {
  return (
    <EngagementMetricsPanelView
      {...useEngagementMetrics(managedServiceId)}
      snsConfigured={snsConfigured}
    />
  );
}

function MessageLogPanel({ managedServiceId }: { managedServiceId: string }) {
  return <MessageLogPanelView {...useMessageLog(managedServiceId)} />;
}

function TemplateManagementPanel({ managedServiceId }: { managedServiceId: string }) {
  return (
    <TemplatesPanelView
      {...useEmailTemplates(managedServiceId)}
      renderStats={(name) => <TemplateStats managedServiceId={managedServiceId} name={name} />}
    />
  );
}

function TemplateStats({ managedServiceId, name }: { managedServiceId: string; name: string }) {
  return <TemplateStatsView {...useTemplateStats(managedServiceId, name)} />;
}
