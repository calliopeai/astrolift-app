"use client";

import { notFound } from "next/navigation";

import { NewFormScreen } from "@/components/screens/forms/NewFormScreen";
import { useNewForm } from "@/components/screens/forms/use-new-form";
import { isRouteEnabled } from "@/lib/route-flags";

export default function NewFormPage() {
  if (!isRouteEnabled("/forms")) notFound();

  return <NewFormScreen {...useNewForm()} />;
}
