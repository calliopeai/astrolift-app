import { BoxIcon, ExternalLinkIcon, HammerIcon, PackageIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

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
  return (
    <PageShell
      title="Build"
      description="Native CI pipelines, image building, and artifact management — coming soon to Astrolift."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <HammerIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">CI Pipelines</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Define build pipelines alongside your app manifest. Astrolift
                runs them on every push, surfaces results in the deploy timeline,
                and gates deploys on passing builds.
              </p>
            </div>
            <Button variant="outline" size="sm" className="w-fit gap-1.5" disabled>
              Coming soon
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <BoxIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Image Builder</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Build Docker images from your source using Buildkit. Images are
                pushed to the platform ECR and tagged automatically — no
                external registry or CI service required.
              </p>
            </div>
            <Button variant="outline" size="sm" className="w-fit gap-1.5" disabled>
              Coming soon
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <PackageIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Artifact Management</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Manage image tags, digests, and retention policies from the
                Astrolift control plane. Audit every image that was deployed,
                when, and by whom.
              </p>
            </div>
            <Button variant="outline" size="sm" className="w-fit gap-1.5" asChild>
              <a
                href="https://astrolift.ai/roadmap"
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLinkIcon className="size-3.5" />
                View roadmap
              </a>
            </Button>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
