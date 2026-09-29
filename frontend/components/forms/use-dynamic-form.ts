"use client";

import { useFormDefinition, useSubmitForm } from "@/graphql/forms/forms.hooks";

import type { DynamicFormSubmitResult } from "./DynamicForm";

/** A published form's definition and its submit mutation. The data half of DynamicForm. */
export function useDynamicForm(slug: string) {
  const { form, loading, error } = useFormDefinition(slug);
  const [submitForm] = useSubmitForm();

  async function onSubmit(payload: Record<string, unknown>): Promise<DynamicFormSubmitResult> {
    const { data } = await submitForm({ variables: { slug, payload } });
    return {
      ok: Boolean(data?.submitForm.ok),
      submissionId: data?.submitForm.data?.id ?? null,
      errors: data?.submitForm.errors ?? [],
    };
  }

  return { formDef: form, loading, error: error?.message ?? null, onSubmit };
}
