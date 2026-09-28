"use client";

import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { useCreateFormDefinition } from "@/graphql/forms/forms.hooks";

export type NewFormValues = {
  name: string;
  slug: string;
  description: string;
};

/**
 * The create-form mutation. `schema` is either the builder's schema object or,
 * in JSON mode, the raw text to parse. Resolves true when the form was created
 * (and navigation to it has started).
 */
export function useNewForm() {
  const router = useRouter();
  const [createForm] = useCreateFormDefinition();

  const onCreate = async (
    values: NewFormValues,
    schema: Record<string, unknown> | string
  ): Promise<boolean> => {
    let finalSchema: Record<string, unknown>;
    if (typeof schema === "string") {
      try {
        finalSchema = JSON.parse(schema);
      } catch {
        toast.error("Invalid JSON schema");
        return false;
      }
    } else {
      finalSchema = schema;
    }

    const { data: result } = await createForm({
      variables: {
        input: {
          name: values.name,
          slug: values.slug,
          description: values.description,
          schema: finalSchema,
        },
      },
    });

    if (result?.createFormDefinition?.ok) {
      toast.success("Form created", { description: `${values.name} is ready as a draft.` });
      router.push(`/forms/${values.slug}`);
      return true;
    }
    for (const e of result?.createFormDefinition?.errors ?? []) {
      toast.error(`${e.field ?? "form"}: ${e.message}`);
    }
    return false;
  };

  return { onCreate };
}
