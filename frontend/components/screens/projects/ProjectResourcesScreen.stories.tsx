import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ProjectResourcesScreen } from "./ProjectResourcesScreen";
import { BUNDLES, LONG, RESOURCES, RESOURCES_EMPTY, SERVICES } from "./projects-detail.fixtures";

const meta: Meta<typeof ProjectResourcesScreen> = {
  title: "Screens/Projects/ProjectResourcesScreen",
  component: ProjectResourcesScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof ProjectResourcesScreen>;

/** Active, mounted, and failed resources; bundles with and without keys. */
export const Full: Story = { args: RESOURCES };

export const Loading: Story = { args: { ...RESOURCES, project: null, loading: true } };

/** The project resolved; its resources are still loading. */
export const ResourcesLoading: Story = {
  args: { ...RESOURCES_EMPTY, resourcesLoading: true },
};

export const Empty: Story = { args: RESOURCES_EMPTY };

export const NotFound: Story = { args: { ...RESOURCES, project: null } };

/** Read-only viewer: no create, attach, reveal, or delete actions. */
export const ReadOnly: Story = {
  args: { ...RESOURCES, canUpdate: false, canWriteSecrets: false, canReadSecrets: false },
};

/**
 * The page has no page-level error state; the closest real ones are a
 * failed resource (Full shows one) and the catalogue failing to load in
 * the Add resource dialog, shown here.
 */
export const CatalogError: Story = {
  args: { ...RESOURCES, catalogEntries: [], catalogError: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Add resource/ }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(
      await body.findByText("The cluster catalogue could not be loaded.")
    ).toBeInTheDocument();
  },
};

export const AddResourceOpen: Story = {
  args: RESOURCES,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Add resource/ }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("Add project resource")).toBeInTheDocument();
  },
};

export const NewBundleOpen: Story = {
  args: RESOURCES,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /New secret bundle/ }));
    const body = within(canvasElement.ownerDocument.body);
    await expect(await body.findByText("New shared secret bundle")).toBeInTheDocument();
  },
};

export const ServiceConsumers: Story = {
  args: { ...RESOURCES, consumerService: SERVICES[0] },
};

export const BundleConsumers: Story = {
  args: { ...RESOURCES, consumerBundle: BUNDLES[0] },
};

export const LongStrings: Story = {
  args: {
    ...RESOURCES,
    project: { ...RESOURCES.project!, name: `Project ${LONG}` },
    services: SERVICES.map((row) => ({
      ...row,
      name: `${row.name}-${LONG}`,
      clusterSlug: `cluster-${LONG}`,
      statusError: row.statusError ? `${row.statusError} ${LONG}` : "",
      operationWorkflowId: row.operationWorkflowId ? `${row.operationWorkflowId}-${LONG}` : "",
      attachments: row.attachments.map((a) => ({
        ...a,
        consumerSlug: `${a.consumerSlug}-${LONG}`,
      })),
    })),
    bundles: BUNDLES.map((row) => ({
      ...row,
      name: `${row.name} ${LONG}`,
      slug: `${row.slug}-${LONG}`,
      keyNames: [...row.keyNames, `A_VERY_LONG_KEY_NAME_${LONG.toUpperCase().replace(/-/g, "_")}`],
    })),
  },
};
