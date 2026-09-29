import { BotIcon } from "lucide-react";

import { StatusDot } from "@/components/StatusDot";
import { cn } from "@/lib/utils";

import { AGENT_STATUS_DOT, AGENT_STATUS_LABEL, type AgentStatusKey } from "./agents-list";

export interface AgentGlyphProps {
  status: AgentStatusKey;
  /** Runs in flight; shown on the glyph when more than one. */
  running?: number;
  className?: string;
}

/**
 * An agent as one small fleet mark: the agent icon with its status dot on
 * the corner. The dot is the only thing that moves, and only while a run is
 * in flight, so motion always means state.
 */
export function AgentGlyph({ status, running = 0, className }: AgentGlyphProps) {
  const label =
    running > 1 ? `${AGENT_STATUS_LABEL[status]}, ${running} runs` : AGENT_STATUS_LABEL[status];
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn(
        "bg-muted text-muted-foreground relative inline-flex size-8 shrink-0 items-center justify-center rounded-sm",
        className
      )}
    >
      <BotIcon className="size-4" aria-hidden />
      <StatusDot status={AGENT_STATUS_DOT[status]} className="absolute -top-0.5 -right-0.5" />
      {running > 1 && (
        <span className="bg-background text-2xs absolute -right-1 -bottom-1 rounded-sm px-0.5 font-mono leading-none">
          {running}
        </span>
      )}
    </span>
  );
}
