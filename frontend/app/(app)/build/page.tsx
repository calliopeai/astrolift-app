import { BuildScreen } from "@/components/screens/dashboard/BuildScreen";

export const metadata = { title: "Build · Astrolift" };

/**
 * Build — CI pipelines, image building, and artifact management gateway.
 *
 * BUILD is the first pillar of the Calliope BROCS stack
 * (Build / Run / Observe / Control / Secure). It provides native CI
 * pipelines, Docker image building, and artifact registry integration
 * so teams can go from source to deployment without leaving Astrolift.
 *
 * This page is the onboarding gateway — shown before the module is
 * enabled, following the same pattern as the Zentinelle / Secure gateway.
 */
export default function BuildPage() {
  return <BuildScreen />;
}
