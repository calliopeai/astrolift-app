import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import {
  DangerAction,
  SettingsPage,
  SettingsSection,
  type SettingsPageProps,
} from "./SettingsPage";
import { useLocalSettingsSection } from "./use-settings-section";

/** The settings archetype (spec 44 §5.3). */
const meta: Meta = { title: "Settings/SettingsPage", parameters: { layout: "padded" } };
export default meta;

const LONG_ARN =
  "arn:aws:iam::123456789012:role/astrolift/clusters/conflict-astrolift/workloads/agents/support-bot/" +
  "service-accounts/support-bot-runtime-executor/permission-boundaries/astrolift-agent-runtime-boundary-v2";

function TextSetting({
  id,
  label,
  help,
  initial,
}: {
  id: string;
  label: string;
  help?: string;
  initial: string;
}) {
  const [saved, setSaved] = React.useState(initial);
  const [value, setValue] = React.useState(initial);
  const [saving, setSaving] = React.useState(false);
  return (
    <SettingsSection
      title={label}
      description={help}
      dirty={value !== saved}
      saving={saving}
      onCancel={() => setValue(saved)}
      onSave={async () => {
        setSaving(true);
        await new Promise((r) => setTimeout(r, 300));
        setSaved(value);
        setSaving(false);
      }}
    >
      <div className="flex min-w-0 flex-col gap-1.5">
        <Label htmlFor={id}>{label}</Label>
        <Input
          id={id}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="font-mono"
        />
      </div>
    </SettingsSection>
  );
}

function Page(props: Partial<SettingsPageProps> & { arn?: string; error?: string }) {
  return (
    <SettingsPage
      sections={[
        {
          id: "central-auth",
          title: "Central auth",
          content: (
            <TextSetting
              id="issuer"
              label="Central auth"
              help="Sign in to every app on this cluster through one identity provider."
              initial="https://auth.conflict.softinfra.net/realms/astrolift"
            />
          ),
        },
        {
          id: "ingress",
          title: "Ingress",
          content: (
            <TextSetting
              id="domain"
              label="Ingress"
              help="The wildcard domain apps are served under."
              initial="*.apps.conflict.softinfra.net"
            />
          ),
        },
        {
          id: "iam",
          title: "Runtime role",
          content: (
            <SettingsSection
              title="Runtime role"
              description="The role workloads assume."
              dirty
              error={props.error}
              onSave={() => {}}
              onCancel={() => {}}
            >
              <div className="flex min-w-0 flex-col gap-1.5">
                <Label htmlFor="role">Role ARN</Label>
                <Input
                  id="role"
                  defaultValue={props.arn ?? "arn:aws:iam::123456789012:role/runtime"}
                  className="font-mono"
                />
              </div>
            </SettingsSection>
          ),
        },
      ]}
      dangerZone={
        <>
          <DangerAction
            title="Detach cluster"
            description="Astrolift stops managing conflict-astrolift. Workloads keep running."
            actionLabel="Detach cluster"
            confirmTitle="Detach conflict-astrolift?"
            confirmDescription="You can attach it again with a new install token."
            onConfirm={() => {}}
          />
          <DangerAction
            title="Delete cluster"
            description="Tears down the cluster and every workload on it."
            actionLabel="Delete cluster"
            confirmTitle="Delete conflict-astrolift?"
            confirmDescription="This cannot be undone."
            reasonLabel="Reason for deleting"
            onConfirm={() => {}}
          />
        </>
      }
      {...props}
    />
  );
}

export const Default: StoryObj = { render: () => <Page /> };

/** No permission: the same fields, disabled, and the permission named. */
export const ReadOnly: StoryObj = {
  render: () => <Page readOnly={{ permission: "cluster.manage" }} />,
};

export const SaveError: StoryObj = {
  render: () => <Page error="The cluster rejected the role: AccessDenied on sts:AssumeRole." />,
};

export const NoDangerZone: StoryObj = { render: () => <Page dangerZone={undefined} /> };

export const LongStrings: StoryObj = {
  render: () => (
    <Page
      arn={LONG_ARN}
      error={`AccessDenied: ${LONG_ARN} is not authorized to perform sts:AssumeRole on ${LONG_ARN}`}
      readOnly={{ permission: "cluster.settings.manage_runtime_identity_and_access_boundaries" }}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6); below md the nav is a select. */
export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="border p-4">
      <Page arn={LONG_ARN} />
    </div>
  ),
};

// ---------------------------------------------------------------------------
// Single-section mode: only the active section is mounted, by ?section=
// ---------------------------------------------------------------------------

function Single({
  initial = null,
  ...props
}: Partial<SettingsPageProps> & { initial?: string | null; arn?: string; error?: string }) {
  const section = useLocalSettingsSection(initial);
  return <Page single={section} {...props} />;
}

/** Heavy sections (a config editor, a shell console) mount one at a time. */
export const SingleSection: StoryObj = { render: () => <Single /> };
export const SingleSectionSelected: StoryObj = { render: () => <Single initial="ingress" /> };
export const SingleSectionDangerZone: StoryObj = {
  render: () => <Single initial="danger-zone" />,
};
/** An unknown `?section=` shows the first section. */
export const SingleSectionUnknownId: StoryObj = {
  render: () => <Single initial="no-such-section" />,
};
export const SingleSectionReadOnly: StoryObj = {
  render: () => <Single initial="iam" readOnly={{ permission: "cluster.manage" }} />,
};
export const SingleSectionLongStrings: StoryObj = {
  render: () => (
    <Single
      initial="iam"
      arn={LONG_ARN}
      error={`AccessDenied: ${LONG_ARN} is not authorized to perform sts:AssumeRole`}
    />
  ),
};
export const SingleSectionWidth768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="border p-4">
      <Single initial="iam" arn={LONG_ARN} />
    </div>
  ),
};
