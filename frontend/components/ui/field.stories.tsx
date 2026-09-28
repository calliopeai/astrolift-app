import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

const meta: Meta = { title: "Atoms/Field" };
export default meta;

/** Label, control, help and error as one unit (spec 44 §7). */
export const States: StoryObj = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-6">
      <Field>
        <FieldLabel htmlFor="f1">App name</FieldLabel>
        <Input id="f1" placeholder="checkout" />
        <FieldDescription>Lowercase letters, numbers and hyphens.</FieldDescription>
      </Field>
      <Field data-invalid>
        <FieldLabel htmlFor="f2">App name</FieldLabel>
        <Input id="f2" defaultValue="Not A Slug!" aria-invalid />
        <FieldError>Use lowercase letters, numbers and hyphens.</FieldError>
      </Field>
    </div>
  ),
};
