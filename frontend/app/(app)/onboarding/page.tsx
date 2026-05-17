import { redirect } from "next/navigation";

export const metadata = {
  title: "Get started · Astrolift",
};

/**
 * Deep-link entry for the onboarding wizard. The wizard itself lives
 * on the dashboard so the auto-open path and the manual-trigger path
 * share one mount point; this route just bounces a visitor to
 * `/dashboard?onboarding=1` so a bookmark or external link still
 * opens the wizard cleanly.
 */
export default function OnboardingPage() {
  redirect("/dashboard?onboarding=1");
}
