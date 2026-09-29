"use client";

import { useParams } from "next/navigation";

import { StageBuilderContainer } from "../../_components/stage-builder";

export default function WorkflowBuilderPage() {
  const { slug } = useParams<{ slug: string }>();
  return <StageBuilderContainer slug={slug} />;
}
