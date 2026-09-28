"use client";

import { notFound, useParams, useRouter } from "next/navigation";

import { useDynamicForm } from "@/components/forms/use-dynamic-form";
import { FormSubmitScreen } from "@/components/screens/forms/FormSubmitScreen";
import { isRouteEnabled } from "@/lib/route-flags";

export default function FormSubmitPage() {
  if (!isRouteEnabled("/forms")) notFound();

  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  const form = useDynamicForm(slug);

  return (
    <FormSubmitScreen
      slug={slug}
      {...form}
      onSuccess={() => {
        setTimeout(() => router.push(`/forms/${slug}`), 2000);
      }}
    />
  );
}
