"use client";

import { FunctionsScreen } from "@/components/screens/functions/FunctionsScreen";
import { useFunctions } from "@/components/screens/functions/use-functions";

export function FunctionsClient() {
  return <FunctionsScreen {...useFunctions()} />;
}
