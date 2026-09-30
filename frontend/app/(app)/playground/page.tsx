import { notFound } from "next/navigation";
import { isRouteEnabled } from "@/lib/route-flags";
import { PlaygroundContainer } from "./playground-container";
export default function PlaygroundPage() {
  if (!isRouteEnabled("/playground")) notFound();
  return <PlaygroundContainer />;
}
