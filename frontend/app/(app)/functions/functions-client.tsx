"use client";

import { FunctionsScreen } from "@/components/screens/functions/FunctionsScreen";
import { FunctionWorkloadsTable } from "@/components/screens/functions/FunctionWorkloadsTable";
import {
  useFunctionsTab,
  useFunctionWorkloads,
} from "@/components/screens/functions/use-functions";

export function FunctionsClient() {
  return <FunctionsScreen {...useFunctionsTab()} fleet={<FleetTab />} />;
}

/** The Fleet tab's workload query runs only while that tab is shown. */
function FleetTab() {
  return <FunctionWorkloadsTable {...useFunctionWorkloads()} />;
}
