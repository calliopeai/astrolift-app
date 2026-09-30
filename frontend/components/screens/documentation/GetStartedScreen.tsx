import Link from "next/link";

export function GetStartedScreen() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Local development setup</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Run the Astrolift control plane from an astrolift-app checkout.
        </p>
      </div>

      <div className="bg-muted/40 text-muted-foreground rounded-md border p-4 text-sm leading-relaxed">
        For your first application deployment, follow the{" "}
        <Link href="/documentation/quickstart" className="text-foreground underline">
          Quickstart
        </Link>
        . The steps here are for contributors running the local control plane. They require Docker
        Desktop or Docker Engine with Compose v2. Review the{" "}
        <Link href="/documentation/configuration" className="text-foreground underline">
          configuration reference
        </Link>{" "}
        and your local environment files before starting the stack.
      </div>

      <section className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">Prepare and start the stack</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Run these commands from the repository root. Bootstrap checks Docker and creates missing
            local environment files from the examples. Fill in required configuration values before
            starting the Compose services. The stack includes the API and UI, PostgreSQL, Redis,
            Temporal, and local storage and email services.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./bootstrap.sh
./run.sh up
./run.sh ps`}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">Migrate and seed the local identity</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Once the API container is running, apply the database migrations and run the development
            identity seed. This creates the local organization, team, project and development
            account used by the local sign-in flow.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./run.sh migrate
./run.sh seed`}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">Explore and inspect</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            The default UI is at http://localhost:3000, the GraphQL API at
            http://localhost:8000/app/gql/config/, and the Temporal UI at http://localhost:8233.
            Check your Compose configuration if you override host ports. Use the command wrapper to
            inspect running containers and logs.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./run.sh urls
./run.sh logs
./run.sh shell`}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h2 className="text-lg font-medium">Run the checks</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Backend tests run against real PostgreSQL and Temporal through the local stack. Ruff
            checks backend formatting and lint; the frontend has TypeScript, ESLint and React tests.
            When changing the GraphQL contract, regenerate its schema and frontend types together.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`./run.sh test
make lint
make schema
cd frontend
npm run codegen
npm run typecheck
npm test`}</code>
          </pre>
        </div>
      </section>
    </article>
  );
}
