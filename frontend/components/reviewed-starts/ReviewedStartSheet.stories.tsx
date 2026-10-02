import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { ReviewedStartSheet, type ReviewedStartSheetProps } from "./ReviewedStartSheet";
import type { InputContract, StartReview } from "./reviewed-start-model";

const empty: InputContract = {
  schema: { type: "object", properties: {}, additionalProperties: false },
  digest: "schema-fixture",
  supported: true,
  error: "",
  acceptsInputs: false,
  supportsSimpleForm: true,
  fields: [],
};
const review: StartReview = {
  id: "019a36a9-0000-7000-8000-000000000001",
  name: "Disposable acceptance workflow",
  revision: "reviewed-fixture-revision",
  enabled: true,
  contract: empty,
};
const args: ReviewedStartSheetProps = {
  kind: "workflow",
  open: true,
  loading: false,
  submitting: false,
  review,
  stamp: null,
  receipt: null,
  error: null,
  uncertain: false,
  restored: false,
  onClose: () => {},
  onReconcile: async () => {},
  onSubmit: async () => {},
};
const meta = {
  title: "Workflows/ReviewedStartSheet",
  component: ReviewedStartSheet,
  parameters: { layout: "fullscreen" },
  args,
} satisfies Meta<typeof ReviewedStartSheet>;
export default meta;
type Story = StoryObj<typeof meta>;
export const NoInputs: Story = {
  play: async () => {
    const sheet = within(document.body);
    await expect(sheet.getByText("This definition accepts no run inputs.")).toBeVisible();
    await expect(sheet.getByRole("button", { name: "Start reviewed run" })).toBeDisabled();
  },
};
export const TypedInputs: Story = {
  args: {
    review: {
      ...review,
      contract: {
        ...empty,
        acceptsInputs: true,
        fields: [
          {
            name: "limit",
            kind: "integer",
            required: true,
            hasDefault: true,
            default: 3,
            sensitive: false,
            simple: true,
            enumValues: [1, 3, 5],
            constraints: { minimum: 1, maximum: 5 },
          },
          {
            name: "credential",
            kind: "string",
            required: false,
            hasDefault: false,
            default: null,
            sensitive: true,
            simple: true,
            enumValues: null,
            constraints: {},
          },
        ],
      },
    },
  },
};
export const ComplexInputContract: Story = {
  args: {
    review: {
      ...review,
      contract: {
        ...empty,
        acceptsInputs: true,
        supportsSimpleForm: false,
        schema: { type: "object", properties: { config: { type: "object" } } },
      },
    },
  },
};
export const UnsupportedContract: Story = {
  args: {
    review: {
      ...review,
      contract: {
        ...empty,
        supported: false,
        supportsSimpleForm: false,
        acceptsInputs: true,
        schema: null,
        error: "Unsupported schema fixture",
      },
    },
  },
};
export const UncertainSubmission: Story = {
  args: {
    uncertain: true,
    restored: true,
    stamp: {
      kind: "workflow",
      targetId: review.id,
      requestId: "same-request-after-lost-response",
      revision: review.revision,
      inputSchemaDigest: empty.digest,
    },
    receipt: {
      id: "execution-fixture",
      temporalWorkflowId: "workflow-fixture",
      temporalRunId: null,
      dispatchStatus: "uncertain",
    },
  },
};
export const AcceptedExecution: Story = {
  args: {
    receipt: {
      id: "execution-fixture",
      temporalWorkflowId: "workflow-fixture",
      temporalRunId: "exact-engine-run",
      dispatchStatus: "submitted",
    },
  },
};
export const PipelineReview: Story = {
  args: {
    kind: "pipeline",
    review: {
      id: review.id,
      name: "Disposable pipeline",
      revision: 4,
      enabled: true,
      defaultBranch: "main",
    },
  },
};
