import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";

import { CentralAuthView, IngressClassView } from "./CentralAuth";
import { CENTRAL_AUTH, INGRESS_CLASS, LONG } from "./fixtures";

const meta: Meta = { title: "Screens/Clusters/Settings/CentralAuth" };
export default meta;

type Story = StoryObj;

export const Configured: Story = { render: () => <CentralAuthView {...CENTRAL_AUTH} /> };

export const Empty: Story = { render: () => <CentralAuthView {...CENTRAL_AUTH} view={null} /> };

export const Editing: Story = {
  render: () => <CentralAuthView {...CENTRAL_AUTH} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Edit" }));
  },
};

export const Saving: Story = {
  render: () => <CentralAuthView {...CENTRAL_AUTH} saving />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Edit" }));
  },
};

export const GermanConfigured: Story = {
  render: () => (
    <NextIntlClientProvider locale="de" messages={de}>
      <CentralAuthView {...CENTRAL_AUTH} />
    </NextIntlClientProvider>
  ),
};

export const SpanishEditing: Story = {
  render: () => (
    <NextIntlClientProvider locale="es" messages={es}>
      <CentralAuthView {...CENTRAL_AUTH} />
    </NextIntlClientProvider>
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole("button", { name: es.clusterSettings.centralAuth.edit })
    );
  },
};

export const WriteRefused: Story = {
  render: () => <CentralAuthView {...CENTRAL_AUTH} onSave={async () => false} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: "Edit" }));
    await userEvent.type(c.getByLabelText("Client secret"), "TEST_ONLY_DRAFT_SECRET");
    await userEvent.click(c.getByRole("button", { name: "Save" }));
  },
};

export const LongStrings: Story = {
  render: () => (
    <CentralAuthView
      {...CENTRAL_AUTH}
      view={{
        ...CENTRAL_AUTH.view,
        auth_proxy_host: `auth.${LONG}.example.com`,
        client_id: LONG,
        discovery_url: `https://${LONG}.example.com/.well-known/openid-configuration`,
        jwks_uri: `https://${LONG}.example.com/jwks.json`,
      }}
    />
  ),
};

export const IngressClass: Story = { render: () => <IngressClassView {...INGRESS_CLASS} /> };

/** Moving to Envoy with no central auth configured would leave apps public. */
export const IngressClassNoGate: Story = {
  render: () => <IngressClassView {...INGRESS_CLASS} centralAuthConfigured={false} />,
};

export const IngressClassUnknown: Story = {
  render: () => <IngressClassView {...INGRESS_CLASS} ingressClass={`custom-${LONG}`} />,
};

export const FrenchIngressReview: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <IngressClassView {...INGRESS_CLASS} />
    </NextIntlClientProvider>
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement),
      t = fr.clusterSettings.ingressClass;
    await userEvent.click(c.getByRole("combobox", { name: t.title }));
    await userEvent.click(
      within(document.body).getByRole("option", { name: t.classes.envoy.label })
    );
    await userEvent.click(c.getByRole("button", { name: t.change }));
  },
};

export const IngressRefused: Story = {
  render: () => (
    <IngressClassView
      {...INGRESS_CLASS}
      onApply={async () => {
        throw new Error("RAW_SERVER_REFUSAL");
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("combobox", { name: "Ingress class" }));
    await userEvent.click(
      within(document.body).getByRole("option", { name: "Envoy edge (central auth)" })
    );
    await userEvent.click(c.getByRole("button", { name: "Change class" }));
    await userEvent.click(
      within(within(document.body).getByRole("alertdialog")).getByRole("button", {
        name: "Change class",
      })
    );
  },
};
