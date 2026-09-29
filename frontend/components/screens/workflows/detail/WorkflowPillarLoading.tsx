import { Loader2Icon } from "lucide-react";

/** Centered spinner shown while a workflow pillar page resolves its workflow. */
export function WorkflowPillarLoading() {
  return (
    <div className="flex justify-center p-12">
      <Loader2Icon className="size-5 animate-spin" />
    </div>
  );
}
