import {
  BotIcon,
  CalendarClockIcon,
  DatabaseIcon,
  LayersIcon,
  NetworkIcon,
  PackageIcon,
  PlayIcon,
  ServerIcon,
  ShapesIcon,
  UsersRoundIcon,
  WorkflowIcon,
  ZapIcon,
  type LucideIcon,
} from "lucide-react";

import { TOPOLOGY_META, type TopologyKind } from "@/lib/topology";
import { cn } from "@/lib/utils";

const ICON: Record<TopologyKind, LucideIcon> = {
  service: ServerIcon,
  "service-data": DatabaseIcon,
  "service-worker": LayersIcon,
  microservices: NetworkIcon,
  "service-agent": UsersRoundIcon,
  agent: BotIcon,
  functions: ZapIcon,
  scheduled: CalendarClockIcon,
  task: PlayIcon,
  workflow: WorkflowIcon,
  mixed: ShapesIcon,
};

export interface AppKindGlyphProps {
  /** The app's shape (lib/topology classifyTopology); null before it has workloads. */
  topology: TopologyKind | null;
  className?: string;
}

/**
 * The app's shape as one icon, the same classification AppView draws its
 * dashboard from. A static mark: it names a kind, it encodes no state.
 */
export function AppKindGlyph({ topology, className }: AppKindGlyphProps) {
  const Icon = topology ? ICON[topology] : PackageIcon;
  const label = topology ? TOPOLOGY_META[topology].label : "No workloads yet";
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn(
        "bg-muted text-muted-foreground inline-flex size-8 shrink-0 items-center justify-center rounded-sm",
        className
      )}
    >
      <Icon className="size-4" aria-hidden />
    </span>
  );
}
