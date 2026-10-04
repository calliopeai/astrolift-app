import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { ModelRuntimeSetupPanel, type ModelRuntimeSetupProps } from "./ModelRuntimeSetupPanel";
export const runtimeSetupFixture: ModelRuntimeSetupProps = {
  scopeKey: "operator:org:cluster",
  allowed: true,
  mode: "CPU",
  loading: false,
  error: null,
  onRetry: () => {},
  onSave: async () => ({ accepted: true }),
  observation: {
    organizationId: "org",
    clusterId: "cluster",
    providerId: "provider",
    clusterVersion: 7,
    providerVersion: 3,
    modes: [
      {
        computeMode: "CPU",
        configured: false,
        reason: null,
        declaration: {
          image: "registry.example/runtime@sha256:" + "a".repeat(64),
          version: "0.15.1",
          packageVersion: "0.15.1+cpu",
          architecture: "AMD64",
          nodeSelector: [{ key: "astrolift.io/runtime", value: "cpu-avx2" }],
          supportedDtypes: ["FLOAT32"],
          defaultDtype: "FLOAT32",
          defaultMaxModelLen: 256,
          maxModelLenCeiling: 256,
          defaultMaxNumSeqs: 1,
          maxNumSeqsCeiling: 1,
          cpuRequestCeiling: "1",
          memoryRequestCeiling: "4Gi",
          gpuCountCeiling: 0,
          hardwareCertified: false,
          hardwareEvidence: null,
        },
      },
    ],
  },
};
const meta = {
  title: "Screens/Models/ModelRuntimeSetupPanel",
  component: ModelRuntimeSetupPanel,
  args: runtimeSetupFixture,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelRuntimeSetupPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const DeclarationOnly: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Configure runtime" }));
    await expect(canvas.getByText(/Saving does not test hardware/)).toBeVisible();
    await userEvent.click(canvas.getByRole("button", { name: "Save declaration" }));
    await expect(
      canvas.getByText(/Runtime declaration saved. No model was deployed/)
    ).toBeVisible();
  },
};
export const ExplicitAttestation: Story = {
  args: {
    observation: {
      ...runtimeSetupFixture.observation!,
      modes: [
        {
          ...runtimeSetupFixture.observation!.modes[0],
          declaration: {
            ...runtimeSetupFixture.observation!.modes[0].declaration!,
            hardwareCertified: true,
            hardwareEvidence: "Native smoke and node inspection receipt",
          },
        },
      ],
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Configure runtime" }));
    await expect(canvas.getByRole("button", { name: "Save declaration" })).toBeDisabled();
    await userEvent.click(canvas.getByRole("checkbox", { name: /I have reviewed/ }));
    await expect(canvas.getByRole("button", { name: "Save declaration" })).toBeEnabled();
    await userEvent.type(canvas.getByLabelText("Evidence reference"), " updated");
    await expect(canvas.getByRole("button", { name: "Save declaration" })).toBeDisabled();
  },
};
export const SavedReadFailed: Story = {
  args: { onSave: async () => ({ accepted: true, refreshFailed: true }) },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Configure runtime" }));
    await userEvent.click(canvas.getByRole("button", { name: "Save declaration" }));
    await expect(canvas.getByText(/saved, but the follow-up read failed/)).toBeVisible();
  },
};
export const Refused: Story = {
  args: {
    onSave: async () => ({ accepted: false, message: "Provider version changed; refresh." }),
  },
};
export const ReadUnavailable: Story = {
  args: { observation: null, error: "Runtime declaration read failed" },
};
export const OrdinaryOrgOwner: Story = { args: { allowed: false, observation: null } };
