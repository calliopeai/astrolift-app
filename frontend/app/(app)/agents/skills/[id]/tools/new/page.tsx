"use client";

import { useParams } from "next/navigation";

import { AddToolScreen } from "@/components/screens/agents/skills/AddToolScreen";
import { useAddTool } from "@/components/screens/agents/skills/use-add-tool";

export default function RegisterToolPage() {
  const { id } = useParams<{ id: string }>();
  return <AddToolScreen {...useAddTool(id)} />;
}
