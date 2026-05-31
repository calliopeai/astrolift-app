import { PageShell } from "@/components/PageShell";
import { PipelinesClient } from "./pipelines-client";

export default function PipelinesPage() {
  return (
    <PageShell
      title="Pipelines"
      description="CI/CD pipelines — build, test, and deploy from your source repository."
    >
      <PipelinesClient />
    </PageShell>
  );
}
