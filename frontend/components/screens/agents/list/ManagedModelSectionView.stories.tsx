import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { managedModelProps } from "./agents-list.fixtures";
import { ManagedModelSectionView } from "./ManagedModelSectionView";

const meta: Meta = { title: "Screens/Agents/List/ManagedModelSectionView" };
export default meta;

type Story = StoryObj;

export const Off: Story = { render: () => <ManagedModelSectionView {...managedModelProps()} /> };

export const On: Story = {
  render: () => <ManagedModelSectionView {...managedModelProps({ managedOn: true })} />,
};

/** A write in flight: the switch is disabled (the loading state). */
export const Saving: Story = {
  render: () => (
    <ManagedModelSectionView {...managedModelProps({ managedOn: true, managedBusy: true })} />
  ),
};

export const FrenchOff: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <ManagedModelSectionView {...managedModelProps()} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseSaving: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <ManagedModelSectionView {...managedModelProps({ managedOn: true, managedBusy: true })} />
    </NextIntlClientProvider>
  ),
};
