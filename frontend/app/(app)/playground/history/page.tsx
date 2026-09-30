import { notFound } from "next/navigation";
import { isRouteEnabled } from "@/lib/route-flags";
import { PlaygroundHistoryContainer } from "./playground-history-container";
export default function PlaygroundHistoryPage() {
  if (!isRouteEnabled("/playground/history")) notFound();
  return <PlaygroundHistoryContainer />;
}
