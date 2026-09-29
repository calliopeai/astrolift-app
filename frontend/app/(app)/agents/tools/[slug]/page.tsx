"use client";

import { useParams } from "next/navigation";

import { ToolDetailScreen } from "@/components/screens/agents/tools/ToolDetailScreen";
import { useToolDetail } from "@/components/screens/agents/tools/use-tool-detail";

export default function ToolDetailPage() {
  // The dynamic segment is named [slug] but we use it as the tool GUID.
  const { slug: id } = useParams<{ slug: string }>();
  const detail = useToolDetail(id);
  return <ToolDetailScreen {...detail} />;
}
