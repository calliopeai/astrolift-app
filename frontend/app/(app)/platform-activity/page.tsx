import { PlatformActivityScreen } from "@/components/screens/platform-activity/PlatformActivityScreen";

export const metadata = { title: "Platform Activity · Astrolift" };

export default function PlatformActivityPage() {
  return <PlatformActivityScreen temporalUiUrl={process.env.NEXT_PUBLIC_TEMPORAL_UI_URL} />;
}
