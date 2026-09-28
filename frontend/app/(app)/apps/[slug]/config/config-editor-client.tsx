"use client";

import { ConfigEditorScreen } from "@/components/screens/apps/config/ConfigEditorScreen";
import { useConfigEditor } from "@/components/screens/apps/config/use-config-editor";

import { AppTabs } from "../components/app-tabs";
import { AgentConfigFormPane } from "./agent-config-form-pane";

/**
 * App config tab. The screen owns the markup; the agent-config builder pane
 * still lives beside this route, so it comes in as a slot.
 */
export function ConfigEditorClient({ slug }: { slug: string }) {
  const editor = useConfigEditor(slug);
  return (
    <ConfigEditorScreen
      {...editor}
      slug={slug}
      tabs={editor.app ? <AppTabs slug={editor.app.slug} active="settings" /> : null}
      renderAgentConfigForm={(props) => <AgentConfigFormPane {...props} />}
    />
  );
}
