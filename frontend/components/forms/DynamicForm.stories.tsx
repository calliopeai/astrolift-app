import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, waitFor, within } from "storybook/test";

import { DynamicForm, type DynamicFormSubmitResult } from "@/components/forms/DynamicForm";
import type { FormDefinition } from "@/graphql/forms/forms.types";

const meta: Meta = { title: "Patterns/Forms/DynamicForm" };
export default meta;

type Story = StoryObj;

const FORM: FormDefinition = {
  id: "form-1",
  name: "Request a cluster",
  slug: "cluster-request",
  description: "Ask the platform team for a new cluster in your region.",
  status: "published",
  version: 3,
  schema: {
    type: "object",
    required: ["team", "region"],
    properties: {
      team: { type: "string", title: "Team" },
      region: { type: "string", title: "Region", enum: ["us-west-2", "eu-west-1"] },
      gpu: { type: "boolean", title: "Needs GPUs" },
      gpu_count: { type: "integer", title: "GPU count" },
    },
  },
  // GPU count shows only when GPUs are asked for.
  logicRules: [
    { condition: { field: "gpu", op: "neq", value: true }, action: "hide", target: "gpu_count" },
  ],
  fieldConfig: {},
  publishedAt: "2026-09-01T00:00:00Z",
  createdAt: "2026-08-20T00:00:00Z",
  updatedAt: "2026-09-01T00:00:00Z",
  submissionCount: 12,
};

const ok = fn(
  async (): Promise<DynamicFormSubmitResult> => ({ ok: true, submissionId: "sub-1", errors: [] })
);

const base = { slug: FORM.slug, formDef: FORM, loading: false, error: null, onSubmit: ok };

export const Ready: Story = {
  render: () => <DynamicForm {...base} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.type(c.getAllByRole("textbox")[0], "payments");
    await userEvent.click(c.getByRole("button", { name: /Submit/ }));
    await waitFor(() => expect(c.getByText("Submitted!")).toBeInTheDocument());
  },
};

export const Loading: Story = {
  render: () => <DynamicForm {...base} formDef={null} loading />,
};

export const NotFound: Story = { render: () => <DynamicForm {...base} formDef={null} /> };

export const LoadError: Story = {
  render: () => <DynamicForm {...base} formDef={null} error="Response not successful: 500" />,
};

export const ServerValidationError: Story = {
  render: () => (
    <DynamicForm
      {...base}
      onSubmit={async () => ({
        ok: false,
        submissionId: null,
        errors: [{ code: "invalid", field: "team", message: "No team called payments exists." }],
      })}
    />
  ),
};
