"use client";

import { useParams } from "next/navigation";

import { StageBuilder } from "@/components/workflows/StageBuilder";

export default function WorkflowBuilderPage() {
  const { slug } = useParams<{ slug: string }>();
  return <StageBuilder slug={slug} />;
}
