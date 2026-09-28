import { ConfigurationScreen } from "@/components/screens/documentation/ConfigurationScreen";
import { CONFIGURATION_GROUPS } from "@/components/screens/documentation/configuration-groups";

export const metadata = {
  title: "Configuration · Documentation · Astrolift",
};

export default function ConfigurationDocPage() {
  return <ConfigurationScreen groups={CONFIGURATION_GROUPS} />;
}
