"use client";

import { notFound } from "next/navigation";

import { PlaygroundScreen } from "@/components/screens/playground/PlaygroundScreen";
import { usePlayground } from "@/components/screens/playground/use-playground";
import { isRouteEnabled } from "@/lib/route-flags";

import { PlaygroundBatchContainer } from "./playground-batch-container";

export default function PlaygroundPage() {
  if (!isRouteEnabled("/playground")) notFound();

  const playground = usePlayground();

  return (
    <PlaygroundScreen
      {...playground}
      batch={<PlaygroundBatchContainer model={playground.model} />}
    />
  );
}
