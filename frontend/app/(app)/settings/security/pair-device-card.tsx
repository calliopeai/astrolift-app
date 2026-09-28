"use client";

import { PairDeviceView } from "@/components/screens/settings/security/PairDevice";
import { usePairDevice } from "@/components/screens/settings/security/use-pair-device";

/** Mobile-device pairing card (#494). Markup lives in PairDeviceView. */
export function PairDeviceCard() {
  return <PairDeviceView {...usePairDevice()} />;
}
