"use client";

import type { ComponentProps } from "react";

import { DynamicForm } from "@/components/forms/DynamicForm";

export type FormSubmitScreenProps = ComponentProps<typeof DynamicForm>;

/** The fill-out page for a published form: DynamicForm in the page frame. */
export function FormSubmitScreen(props: FormSubmitScreenProps) {
  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <DynamicForm {...props} />
    </div>
  );
}
