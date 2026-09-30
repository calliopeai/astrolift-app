import Link from "next/link";

export function IntroductionScreen() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Introduction</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Build, deploy and operate with Astrolift.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">What is Astrolift?</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Astrolift is a control plane for applications and agents running on Kubernetes. Connect
          your clusters and source repositories, register an Astrolift manifest, and manage
          deployments, environment configuration and managed services from one dashboard or the CLI.
        </p>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The control plane records desired configuration and deployment history. Cluster agents
          report observed runtime state; unavailable observations remain distinct from healthy or
          failed workloads.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Key concepts</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">Organizations, teams and projects</strong> organize
            ownership and access to apps, clusters and shared resources.
          </li>
          <li>
            <strong className="text-foreground">Applications and manifests</strong> describe
            workloads, configuration and service bindings. A registered app connects that definition
            to its source repository.
          </li>
          <li>
            <strong className="text-foreground">Environments</strong> select runtime placement and
            environment-specific configuration for an app.
          </li>
          <li>
            <strong className="text-foreground">Deployments</strong> track image versions, approval
            requirements, execution and history for an environment.
          </li>
          <li>
            <strong className="text-foreground">Roles, policies and credentials</strong> control
            permitted operations and resource scope. A token cannot expand its holder&apos;s access.
          </li>
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Control plane and runtime</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The dashboard and CLI call the Django and Strawberry GraphQL control plane. PostgreSQL
          stores platform records, Redis supports caching, and Temporal coordinates long-running
          lifecycle work. Applications run on connected Kubernetes clusters rather than inside the
          dashboard process.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Where to start</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <Link href="/documentation/quickstart" className="text-foreground underline">
              Quickstart
            </Link>{" "}
            walks through connecting a cluster and source, registering an app and deploying it.
          </li>
          <li>
            <Link href="/documentation/cluster-prerequisites" className="text-foreground underline">
              Cluster prerequisites
            </Link>{" "}
            describes the runtime services your cluster needs.
          </li>
          <li>
            <Link href="/documentation/get-started" className="text-foreground underline">
              Local development setup
            </Link>{" "}
            explains how contributors run the control plane locally.
          </li>
        </ul>
      </section>
    </article>
  );
}
