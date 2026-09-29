"use client";

import { ConfigEditorClient } from "@/app/(app)/apps/[slug]/config/config-editor-client";
import { ManifestPreviewClient } from "@/app/(app)/apps/[slug]/manifest/manifest-preview-client";
import { WebhooksClient } from "@/app/(app)/webhooks/webhooks-client";
import { AgentConfigurationTab } from "@/components/screens/agents/detail/AgentConfigurationTab";
import { useSettingsSection } from "@/components/settings/use-settings-section";

import { AgentModelAccessCard } from "./agent-model-access-card";
import { BuildContent } from "./build-content";
import { ControlContent } from "./control-content";

/**
 * The Configuration tab's containers. An agent IS a RegisteredApp, so the
 * config editor, manifest and webhooks are the app's own clients; the
 * screen mounts only the section in `?section=`, so only its hooks run.
 */
export function ConfigurationContent({ slug }: { slug: string }) {
  return (
    <AgentConfigurationTab
      section={useSettingsSection()}
      slots={{
        build: <BuildContent />,
        "run-mode": <ControlContent />,
        config: <ConfigEditorClient slug={slug} />,
        manifest: <ManifestPreviewClient slug={slug} />,
        webhooks: <WebhooksClient appSlug={slug} />,
        "model-access": <AgentModelAccessCard agentSlug={slug} />,
      }}
    />
  );
}
