"use client";

import { SupportScreen } from "@/components/screens/support/SupportScreen";
import { useSupport } from "@/components/screens/support/use-support";

export function SupportClient() {
  const state = useSupport();
  return (
    <SupportScreen
      {...state}
      onCreate={() => void state.onCreate()}
      onSendReply={() => void state.onSendReply()}
    />
  );
}
