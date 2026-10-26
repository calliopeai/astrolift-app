"use client";

import { BrainConnectTab } from "@/components/screens/brains/detail/BrainConnectTab";

import { useFramedBrain } from "./framed-brain";

/** The Connect tab: the frame's Connect, which issues the token on the button only. */
export function ConnectContent() {
  const framed = useFramedBrain();
  return (
    <BrainConnectTab
      slug={framed.brain.slug}
      endpoints={framed.brain.environments}
      connecting={framed.connecting}
      onConnect={framed.onConnect}
      onOpenPortal={framed.onOpenPortal}
    />
  );
}
