import { notFound } from "next/navigation";
import { isRouteEnabled } from "@/lib/route-flags";
import { PlaygroundStarredContainer } from "./playground-starred-container";
export default function PlaygroundStarredPage() {
  if (!isRouteEnabled("/playground/starred")) notFound();
  return <PlaygroundStarredContainer />;
}
