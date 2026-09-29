import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export function SourceProvidersDocScreen() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Source providers</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Connect Astrolift to GitHub or GitLab so a <code>git push</code> triggers a deploy.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">When you need this</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          You want to register an app whose source lives on a hosted Git provider, and have
          Astrolift watch a branch (or pull requests) so merges and pushes turn into deploys
          automatically. Without a connected source provider, apps are still deployable from
          manifests and registry images, but the push-to-deploy loop is unavailable.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Prerequisites</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">Permission.</strong> An operator role that includes{" "}
            <code>scm.connect</code> (Owner and Admin have it by default).
          </li>
          <li>
            <strong className="text-foreground">
              Admin access to the GitHub org or GitLab group.
            </strong>{" "}
            The Astrolift App is installed at the org / group level, not on individual users, so an
            org owner has to approve it.
          </li>
          <li>
            <strong className="text-foreground">Your install&apos;s public URL.</strong> GitHub and
            GitLab need to reach Astrolift to deliver webhooks. The backend reads this from{" "}
            <code>APP_BASE_URL</code>; verify it points at the public hostname users hit, not a
            private IP.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">
          GitHub{" "}
          <Badge className="ml-1" variant="secondary">
            recommended
          </Badge>
        </h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The fastest path. Astrolift submits a GitHub App manifest on your behalf; you only have to
          approve the permissions and pick which repos the App can see.
        </p>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 1 — Open Source providers</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Go to{" "}
            <Link
              href="/settings/source-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Settings · Source providers
            </Link>{" "}
            and click <strong>Connect to GitHub</strong>.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 2 — Enter your GitHub org slug</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Type the org slug (the part of the URL after <code>github.com/</code>) and click{" "}
            <strong>Continue on GitHub</strong>. Astrolift submits a one-shot manifest containing
            the App name, callback URLs, webhook URL, and the permissions it requests.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 3 — Approve the App on GitHub</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            GitHub shows the permissions the Astrolift App requires:
          </p>
          <ul className="text-muted-foreground flex flex-col gap-1 text-sm">
            <li>
              <code>contents: read</code> — clone source at deploy time
            </li>
            <li>
              <code>metadata: read</code> — list repos and branches
            </li>
            <li>
              <code>pull_requests: write</code> — comment with preview URLs
            </li>
            <li>
              <code>checks: write</code> — report build / deploy status on commits
            </li>
          </ul>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Click <strong>Create GitHub App for &lt;your org&gt;</strong>. GitHub creates the App,
            installs it on the org, and redirects you back to Astrolift.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 4 — Pick repositories during onboarding</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            The connection now appears under <strong>Hosts</strong>. When you register a new app
            from{" "}
            <Link href="/apps/new" className="text-foreground underline-offset-2 hover:underline">
              Apps · Register app
            </Link>
            , its onboarding wizard browses the repos the App was granted access to. To widen or
            narrow that set, edit the install on GitHub (
            <code>https://github.com/organizations/&lt;org&gt;/settings/installations</code>).
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">
          GitLab <Badge variant="outline">guided manual</Badge>
        </h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          GitLab does not have a manifest API equivalent to GitHub Apps, so this flow is a few extra
          clicks: register an OAuth application on GitLab, then paste the credentials back into
          Astrolift.
        </p>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 1 — Open Source providers</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Go to{" "}
            <Link
              href="/settings/source-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Settings · Source providers
            </Link>{" "}
            and click <strong>Connect to GitLab</strong>.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 2 — Enter group and instance URL</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Type your GitLab group slug. The instance URL defaults to{" "}
            <code>https://gitlab.com</code>; change it if you run a self-managed instance.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">
            Step 3 — Register an Astrolift application on GitLab
          </h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Click <strong>Open GitLab Applications</strong>. In the new tab, create a Group-owned
            application with these settings:
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`Name:          Astrolift
Redirect URI:  <copy from the Astrolift dialog>
Scopes:        api, read_repository, write_repository`}</code>
          </pre>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Save. GitLab shows the <strong>Application ID</strong> and <strong>Secret</strong>{" "}
            exactly once.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Step 4 — Paste the credentials back</h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Return to Astrolift, paste the Application ID and Secret into the dialog, and save. The
            connection appears under <strong>Hosts</strong> and you can register apps from any
            project in the group.
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              &ldquo;Hook url is not supported because it isn&apos;t reachable over the public
              Internet.&rdquo;
            </strong>{" "}
            GitHub or GitLab can&apos;t reach the callback. This is operator-side: set{" "}
            <code>APP_BASE_URL</code> in the backend environment to the public URL of your install
            (the same hostname users hit), then re-run the connect flow.
          </li>
          <li>
            <strong className="text-foreground">
              Can&apos;t see private org repos in the onboarding wizard.
            </strong>{" "}
            The GitHub App install was scoped to a subset of repos. Visit{" "}
            <code>github.com/organizations/&lt;org&gt;/settings/installations</code>, open the
            Astrolift App, and either select the specific repos you need or choose{" "}
            <strong>All repositories</strong>.
          </li>
          <li>
            <strong className="text-foreground">Pushes aren&apos;t triggering deploys.</strong> Open
            the app&apos;s <strong>Webhooks</strong> tab — incoming SCM webhooks land in the recent
            deliveries list with response status and body. A 401 means the signing secret drifted;
            re-rotate from{" "}
            <Link
              href="/settings/source-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Source providers
            </Link>
            .
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/webhooks"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Webhooks
            </Link>{" "}
            — outbound notifications when builds and deploys finish.
          </li>
          <li>
            <Link
              href="/documentation/custom-domains"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Custom domains
            </Link>{" "}
            — point a branded hostname at the app you just connected.
          </li>
        </ul>
      </section>
    </article>
  );
}
