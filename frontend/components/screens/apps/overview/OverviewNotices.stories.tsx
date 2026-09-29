import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { InfoIcon } from "lucide-react";

import {
  AUTOWIRE,
  AUTOWIRE_NOT_CONNECTED,
  CONFIG_DRIFT,
  DEREGISTER,
  GITHUB_CONNECT,
  LONG,
  PROVISIONING,
  REPROVISION,
} from "./app-overview-banners.fixtures";
import { AutowireStatusBannerView } from "./AutowireStatusBanner";
import { ConfigDriftBannerView } from "./ConfigDriftBanner";
import { DeregisterPendingBannerView } from "./DeregisterPendingBanner";
import { GithubConnectCalloutView } from "./GithubConnectCallout";
import { Notice, OverviewNotices } from "./OverviewNotices";
import { ProvisioningProgressView } from "./ProvisioningProgress";
import { ReprovisionCalloutView } from "./ReprovisionCallout";

/**
 * The overview's one notices slot: every notice at once, as quiet rows in a
 * single frame, never a stack of competing banners.
 */
const meta: Meta = { title: "Screens/Apps/Overview/OverviewNotices" };
export default meta;

type Story = StoryObj;

/** Every notice the overview can raise. */
export const Full: Story = {
  render: () => (
    <OverviewNotices>
      <DeregisterPendingBannerView {...DEREGISTER} />
      <ProvisioningProgressView {...PROVISIONING} />
      <ReprovisionCalloutView {...REPROVISION} />
      <ConfigDriftBannerView {...CONFIG_DRIFT} />
      <GithubConnectCalloutView {...GITHUB_CONNECT} />
      <AutowireStatusBannerView {...AUTOWIRE} />
    </OverviewNotices>
  ),
};

/** One notice: the frame fits it. */
export const Single: Story = {
  render: () => (
    <OverviewNotices>
      <AutowireStatusBannerView {...AUTOWIRE} autowire={AUTOWIRE_NOT_CONNECTED} />
    </OverviewNotices>
  ),
};

/**
 * Nothing to say: the frame hides itself (a sibling keeps the canvas
 * non-empty). The slot has no loading or error state; each notice waits for
 * its data and renders nothing until then.
 */
export const Empty: Story = {
  render: () => (
    <div>
      <OverviewNotices>
        <DeregisterPendingBannerView {...DEREGISTER} msRemaining={null} />
      </OverviewNotices>
      <p className="text-muted-foreground text-xs">No notices: the slot is hidden above.</p>
    </div>
  ),
};

/** A notice outside the slot draws its own border (the Settings tab). */
export const Standalone: Story = {
  render: () => <DeregisterPendingBannerView {...DEREGISTER} />,
};

export const LongStrings: Story = {
  render: () => (
    <OverviewNotices>
      <Notice tone="danger" icon={InfoIcon} title={LONG} description={`${LONG} ${LONG}`}>
        <p className="font-mono [overflow-wrap:anywhere]">{LONG.repeat(3)}</p>
      </Notice>
      <ConfigDriftBannerView
        {...CONFIG_DRIFT}
        drift={{ ...CONFIG_DRIFT.drift, environmentName: LONG, fields: [LONG] }}
      />
    </OverviewNotices>
  ),
};

export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <OverviewNotices>
        <ReprovisionCalloutView {...REPROVISION} />
        <AutowireStatusBannerView {...AUTOWIRE} />
      </OverviewNotices>
    </div>
  ),
};
