"use client";

import { notFound, useParams } from "next/navigation";

import { FormDetailScreen } from "@/components/screens/forms/FormDetailScreen";
import { useFormDetail } from "@/components/screens/forms/use-form-detail";
import { isRouteEnabled } from "@/lib/route-flags";

export default function FormDetailPage() {
  if (!isRouteEnabled("/forms")) notFound();

  const { slug } = useParams<{ slug: string }>();
  return <FormDetailScreen {...useFormDetail(slug)} />;
}
