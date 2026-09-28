import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useForm } from "react-hook-form";

import { DynamicField } from "@/components/forms/field-registry";

/** One schema-driven field of each kind the registry renders. */
const meta: Meta = { title: "Patterns/Forms/DynamicField" };
export default meta;

const FIELDS: Record<string, Record<string, unknown>> = {
  name: { type: "string", title: "Name" },
  count: { type: "integer", title: "Replicas" },
  enabled: { type: "boolean", title: "Enabled" },
  region: { type: "string", title: "Region", enum: ["us-west-2", "eu-west-1"] },
};

function Demo() {
  const { register, control, formState } = useForm();
  return (
    <div className="flex max-w-sm flex-col gap-4">
      {Object.entries(FIELDS).map(([name, schema]) => (
        <DynamicField
          key={name}
          name={name}
          schema={schema}
          register={register}
          control={control}
          errors={formState.errors}
        />
      ))}
    </div>
  );
}

export const Kinds: StoryObj = { render: () => <Demo /> };
