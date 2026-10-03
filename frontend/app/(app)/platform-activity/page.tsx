import { InstancesClient } from "../workflows/instances/instances-client";

export const metadata = { title: "Platform Activity · Astrolift" };

export default function PlatformActivityPage() {
  return <InstancesClient platformActivity />;
}
