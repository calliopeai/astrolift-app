"use client";

import { notFound } from "next/navigation";

import { FormsListScreen } from "@/components/screens/forms/FormsListScreen";
import { useFormDefinitions } from "@/graphql/forms/forms.hooks";
import { isRouteEnabled } from "@/lib/route-flags";

export default function FormsPage() {
  if (!isRouteEnabled("/forms")) notFound();

  const { forms, loading, error } = useFormDefinitions();

  return <FormsListScreen forms={forms} loading={loading} error={error} />;
}
