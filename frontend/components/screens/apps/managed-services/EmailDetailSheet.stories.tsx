import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import {
  ALERT_RULES,
  COST,
  EMAIL_DETAIL,
  EMAIL_DETAIL_LONG,
  EMAIL_DETAIL_UNSUPPORTED,
  EMAIL_SHEET,
  ENGAGEMENT,
  MESSAGE_LOG,
  MESSAGES_LONG,
  SENDER_CONFIG,
  SUPPRESSION,
  TEMPLATE_STATS,
  TEMPLATES,
} from "./app-managed-services.fixtures";
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
} from "./EmailDetailSheet";

const meta: Meta = {
  title: "Screens/Apps/ManagedServices/EmailDetailSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const stats = () => <TemplateStatsView {...TEMPLATE_STATS} />;

const panels = {
  cost: <CostPanelView {...COST} />,
  suppression: <SuppressionPanelView {...SUPPRESSION} />,
  senderConfig: <SenderConfigPanelView {...SENDER_CONFIG} />,
  alertRules: <AlertRulesPanelView {...ALERT_RULES} />,
  engagement: <EngagementMetricsPanelView {...ENGAGEMENT} />,
  messageLog: <MessageLogPanelView {...MESSAGE_LOG} />,
  templates: <TemplatesPanelView {...TEMPLATES} renderStats={stats} />,
};

export const Full: Story = {
  render: () => <EmailDetailSheetView {...EMAIL_SHEET} panels={panels} />,
};

/** Identity, DNS auth and sender settings: the second section. */
export const IdentityAndSending: Story = {
  render: () => <EmailDetailSheetView {...EMAIL_SHEET} panels={panels} defaultSection="sending" />,
};

/** Each list has a section of its own, so one shows at a time. */
export const Suppressions: Story = {
  render: () => <EmailDetailSheetView {...EMAIL_SHEET} panels={panels} />,
  play: async () => {
    // The sheet portals to the document body.
    const c = within(document.body);
    await userEvent.click(c.getByRole("tab", { name: "Suppressions" }));
    await expect(c.getByRole("tab", { name: "Suppressions" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await expect(c.getByText("Suppression list")).toBeInTheDocument();
    await expect(c.queryByText("Recent messages")).toBeNull();
  },
};

export const AlertRules: Story = {
  render: () => <EmailDetailSheetView {...EMAIL_SHEET} panels={panels} defaultSection="alerts" />,
};

export const Messages: Story = {
  render: () => <EmailDetailSheetView {...EMAIL_SHEET} panels={panels} defaultSection="messages" />,
};

export const Templates: Story = {
  render: () => (
    <EmailDetailSheetView {...EMAIL_SHEET} panels={panels} defaultSection="templates" />
  ),
};

export const Loading: Story = {
  render: () => <EmailDetailSheetView {...EMAIL_SHEET} detail={null} loading />,
};

/** The detail query answered null: deprovisioned, or the driver is unreachable. */
export const Unavailable: Story = {
  render: () => <EmailDetailSheetView {...EMAIL_SHEET} detail={null} />,
};

/** A backend (GCP here) that exposes none of the SES panels: every one shades. */
export const Unsupported: Story = {
  render: () => (
    <EmailDetailSheetView
      {...EMAIL_SHEET}
      serviceConfig={{}}
      detail={EMAIL_DETAIL_UNSUPPORTED}
      panels={{
        ...panels,
        suppression: <SuppressionPanelView {...SUPPRESSION} detail={EMAIL_DETAIL_UNSUPPORTED} />,
        engagement: <EngagementMetricsPanelView {...ENGAGEMENT} snsConfigured={false} />,
      }}
    />
  ),
};

/** Sandbox account with a failing identity and a quota close to the ceiling. */
export const Sandbox: Story = {
  render: () => (
    <EmailDetailSheetView
      {...EMAIL_SHEET}
      detail={{
        ...EMAIL_DETAIL,
        quota: { maxSendRate: 1, max24HourSend: 200, sentLast24h: 190 },
        accountStatus: {
          sendingEnabled: true,
          productionAccess: false,
          reputationScore: 0.41,
          bounceRatePct: 8.7,
          complaintRatePct: 0.6,
        },
        identityVerification: {
          ...EMAIL_DETAIL.identityVerification!,
          status: "Failed",
          dkimTokens: [],
        },
        dnsAuthStatus: {
          ...EMAIL_DETAIL.dnsAuthStatus!,
          overall: "RED",
          spf: {
            protocol: "SPF",
            outcome: "RED",
            records: [],
            message: "No SPF record includes amazonses.com.",
          },
        },
      }}
      panels={panels}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <EmailDetailSheetView
      {...EMAIL_SHEET}
      serviceName="transactional-mail-for-the-storefront-with-a-deliberately-long-service-name"
      detail={EMAIL_DETAIL_LONG}
      panels={{
        ...panels,
        suppression: <SuppressionPanelView {...SUPPRESSION} detail={EMAIL_DETAIL_LONG} />,
        messageLog: <MessageLogPanelView {...MESSAGE_LOG} messages={MESSAGES_LONG} />,
      }}
    />
  ),
};

export const LongMessages: Story = {
  render: () => (
    <EmailDetailSheetView
      {...EMAIL_SHEET}
      serviceName="transactional-mail-for-the-storefront-with-a-deliberately-long-service-name"
      detail={EMAIL_DETAIL_LONG}
      defaultSection="messages"
      panels={{
        ...panels,
        messageLog: <MessageLogPanelView {...MESSAGE_LOG} messages={MESSAGES_LONG} />,
      }}
    />
  ),
};

/** The unsupported backend's suppression section: the shaded panel, not a list. */
export const UnsupportedSuppressions: Story = {
  render: () => (
    <EmailDetailSheetView
      {...EMAIL_SHEET}
      serviceConfig={{}}
      detail={EMAIL_DETAIL_UNSUPPORTED}
      defaultSection="suppressions"
      panels={{
        ...panels,
        suppression: <SuppressionPanelView {...SUPPRESSION} detail={EMAIL_DETAIL_UNSUPPORTED} />,
      }}
    />
  ),
};

// ── Panels on their own ────────────────────────────────────────────

export const CostLoading: Story = {
  render: () => <CostPanelView {...COST} mtdSum={null} trailingSum={null} loading />,
};

export const CostEmpty: Story = {
  render: () => <CostPanelView {...COST} mtdSum={null} trailingSum={null} />,
};

export const SuppressionEmpty: Story = {
  render: () => (
    <SuppressionPanelView {...SUPPRESSION} detail={{ ...EMAIL_DETAIL, suppressionEntries: [] }} />
  ),
};

export const SuppressionBusy: Story = {
  render: () => <SuppressionPanelView {...SUPPRESSION} adding removing />,
};

export const SenderConfigEmpty: Story = {
  render: () => <SenderConfigPanelView {...SENDER_CONFIG} serviceConfig={{}} />,
};

export const AlertRulesLoading: Story = {
  render: () => <AlertRulesPanelView {...ALERT_RULES} rules={[]} loading />,
};

export const AlertRulesEmpty: Story = {
  render: () => <AlertRulesPanelView {...ALERT_RULES} rules={[]} />,
};

export const EngagementLoading: Story = {
  render: () => <EngagementMetricsPanelView {...ENGAGEMENT} metrics={null} loading />,
};

/** SNS not configured and nothing sent: the setup hint plus the empty line. */
export const EngagementEmpty: Story = {
  render: () => <EngagementMetricsPanelView {...ENGAGEMENT} metrics={null} snsConfigured={false} />,
};

/** Bounce and complaint rates past their thresholds turn the tiles red. */
export const EngagementUnhealthy: Story = {
  render: () => (
    <EngagementMetricsPanelView
      {...ENGAGEMENT}
      metrics={{ ...ENGAGEMENT.metrics!, bounceRatePct: 11.2, complaintRatePct: 0.3 }}
    />
  ),
};

export const MessagesLoading: Story = {
  render: () => <MessageLogPanelView {...MESSAGE_LOG} messages={[]} loading />,
};

export const MessagesEmpty: Story = {
  render: () => <MessageLogPanelView {...MESSAGE_LOG} messages={[]} />,
};

export const TemplatesLoading: Story = {
  render: () => <TemplatesPanelView {...TEMPLATES} templates={[]} loading renderStats={stats} />,
};

export const TemplatesEmpty: Story = {
  render: () => <TemplatesPanelView {...TEMPLATES} templates={[]} renderStats={stats} />,
};

export const TemplateStats: Story = {
  render: () => <TemplateStatsView {...TEMPLATE_STATS} />,
};

export const TemplateStatsLoading: Story = {
  render: () => <TemplateStatsView {...TEMPLATE_STATS} points={[]} loading />,
};

export const TemplateStatsEmpty: Story = {
  render: () => <TemplateStatsView {...TEMPLATE_STATS} points={[]} />,
};
