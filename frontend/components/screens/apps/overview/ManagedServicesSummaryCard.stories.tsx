import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import {
  CONNECTION,
  COPY,
  DEPTH,
  DIALOG_BASE,
  EMAIL,
  MANAGED_SERVICES_HREF,
  OBJECT_STORE,
  OBJECTS,
  POSTGRES,
  QUEUE,
  REFRESH,
  SEND,
  SERVICES,
  SERVICES_LONG,
} from "./app-overview-cards-b.fixtures";
import {
  ListObjectsDialogView,
  type ManagedServiceDialogSlots,
  ManagedServicesSummaryView,
  QueueDepthDialogView,
  RevealConnectionDialogView,
  SendTestEmailDialogView,
} from "./ManagedServicesSummaryCard";

const dialogs: ManagedServiceDialogSlots = {
  reveal: (p) => (
    <RevealConnectionDialogView {...p} revealed={CONNECTION} loading={false} onCopy={COPY} />
  ),
  sendEmail: (p) => <SendTestEmailDialogView {...p} sending={false} onSend={SEND} />,
  objects: (p) => (
    <ListObjectsDialogView {...p} result={OBJECTS} loading={false} onRefresh={REFRESH} />
  ),
  depth: (p) => <QueueDepthDialogView {...p} result={DEPTH} loading={false} onRefresh={REFRESH} />,
};

const meta: Meta<typeof ManagedServicesSummaryView> = {
  title: "Screens/Apps/Overview/ManagedServicesSummaryCard",
  component: ManagedServicesSummaryView,
  args: {
    loading: false,
    services: SERVICES,
    managedServicesHref: MANAGED_SERVICES_HREF,
    dialogs,
  },
  decorators: [
    (Story) => (
      <div className="p-6">
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof ManagedServicesSummaryView>;

export const Full: Story = {};

export const Loading: Story = { args: { loading: true, services: [] } };

export const Empty: Story = { args: { services: [] } };

export const LongStrings: Story = { args: { services: SERVICES_LONG } };

// ─── dialogs, open ──────────────────────────────────────────────────

export const RevealConnection: StoryObj = {
  render: () => (
    <RevealConnectionDialogView
      {...DIALOG_BASE}
      svc={POSTGRES}
      revealed={CONNECTION}
      loading={false}
      onCopy={COPY}
    />
  ),
};

export const RevealConnectionLoading: StoryObj = {
  render: () => (
    <RevealConnectionDialogView
      {...DIALOG_BASE}
      svc={POSTGRES}
      revealed={null}
      loading
      onCopy={COPY}
    />
  ),
};

/** A failed reveal toasts and leaves the dialog on its loading line. */
export const RevealConnectionFailed: StoryObj = {
  render: () => (
    <RevealConnectionDialogView
      {...DIALOG_BASE}
      svc={POSTGRES}
      revealed={null}
      loading={false}
      onCopy={COPY}
    />
  ),
};

export const SendTestEmail: StoryObj = {
  render: () => (
    <SendTestEmailDialogView {...DIALOG_BASE} svc={EMAIL} sending={false} onSend={SEND} />
  ),
};

export const SendTestEmailSending: StoryObj = {
  render: () => <SendTestEmailDialogView {...DIALOG_BASE} svc={EMAIL} sending onSend={SEND} />,
};

export const ListObjects: StoryObj = {
  render: () => (
    <ListObjectsDialogView
      {...DIALOG_BASE}
      svc={OBJECT_STORE}
      result={OBJECTS}
      loading={false}
      onRefresh={REFRESH}
    />
  ),
};

export const ListObjectsEmpty: StoryObj = {
  render: () => (
    <ListObjectsDialogView
      {...DIALOG_BASE}
      svc={OBJECT_STORE}
      result={{ ...OBJECTS, objects: [], cacheAgeSeconds: null, truncated: false }}
      loading={false}
      onRefresh={REFRESH}
    />
  ),
};

export const ListObjectsLoading: StoryObj = {
  render: () => (
    <ListObjectsDialogView
      {...DIALOG_BASE}
      svc={OBJECT_STORE}
      result={null}
      loading
      onRefresh={REFRESH}
    />
  ),
};

export const QueueDepth: StoryObj = {
  render: () => (
    <QueueDepthDialogView
      {...DIALOG_BASE}
      svc={QUEUE}
      result={DEPTH}
      loading={false}
      onRefresh={REFRESH}
    />
  ),
};

/** No snapshot came back (or the load failed): the dialog's loading line. */
export const QueueDepthNoResult: StoryObj = {
  render: () => (
    <QueueDepthDialogView
      {...DIALOG_BASE}
      svc={QUEUE}
      result={null}
      loading={false}
      onRefresh={REFRESH}
    />
  ),
};

export const QueryFailed: Story = {
  args: { services: [], error: "Permission denied while loading services", onRetry: () => {} },
};

export const FrenchEmailWidth768: StoryObj = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <div style={{ width: 768 }}>
        <SendTestEmailDialogView {...DIALOG_BASE} svc={EMAIL} sending={false} onSend={SEND} />
      </div>
    </NextIntlClientProvider>
  ),
};
export const JapaneseObjectsWidth768: StoryObj = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <div style={{ width: 768 }}>
        <ListObjectsDialogView
          {...DIALOG_BASE}
          svc={OBJECT_STORE}
          result={OBJECTS}
          loading={false}
          onRefresh={REFRESH}
        />
      </div>
    </NextIntlClientProvider>
  ),
};
