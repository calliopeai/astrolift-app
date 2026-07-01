import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

export const metadata = {
  title: "Policies · Documentation · Astrolift",
};

export default function PoliciesDocPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">ABAC policies</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Fine-grained access rules that layer on top of role bindings.
          Policies <strong>never grant</strong> beyond a role — they only
          carve out additional <em>allow</em> or <em>deny</em> conditions.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">When you need this</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Astrolift&apos;s built-in roles cover most teams. Reach for
          policies when you need something the role catalog can&apos;t
          express. For example:
        </p>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            Only senior engineers can deploy to apps named <code>prod-*</code>.
          </li>
          <li>
            Nobody can write secrets between 22:00 and 06:00 UTC unless
            they&apos;re paged.
          </li>
          <li>
            Pre-production deploys require an MFA factor that&apos;s less
            than 60 minutes old.
          </li>
          <li>
            Contractors can read everything in a project but can&apos;t
            mutate anything that touches billing.
          </li>
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Prerequisites</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">Permission.</strong>{" "}
            <code>policy.create</code> (Owner and Admin have it).
          </li>
          <li>
            <strong className="text-foreground">
              Mental model of ABAC vs RBAC.
            </strong>{" "}
            RBAC asks &ldquo;does this user&apos;s role include this
            permission?&rdquo; ABAC asks &ldquo;given the attributes of this
            request (who, what, when, where), does any policy say
            <em> deny</em>?&rdquo; A request only succeeds if RBAC allows it
            and no policy denies it.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Create a policy</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Open{" "}
          <Link
            href="/settings/policies"
            className="text-foreground underline-offset-2 hover:underline"
          >
            Settings · Policies
          </Link>{" "}
          and click <strong>New policy</strong>. Each policy has the
          following fields:
        </p>
      </section>

      <div className="grid gap-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              1. Scope level{" "}
              <Badge variant="outline" className="ml-1">
                ORG · TEAM · PROJECT · APP
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            The breadth of the policy. ORG policies evaluate on every
            request; APP policies only evaluate when the request targets that
            specific app. Narrower scope = less evaluation cost and easier to
            reason about.
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              2. Effect{" "}
              <Badge
                className="bg-success/15 text-success-fg ml-1"
                variant="secondary"
              >
                ALLOW
              </Badge>{" "}
              <Badge
                className="bg-danger/15 text-danger-fg ml-1"
                variant="secondary"
              >
                DENY
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            What happens when the policy matches. DENY always wins — if any
            policy at any scope denies, the request fails, even if a
            narrower-scope ALLOW also matches.
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">3. Action pattern</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            Glob over permission slugs. <code>app.deploy</code> matches one
            exact slug; <code>app.*</code> matches every action on the app
            resource; <code>*</code> matches everything.
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">4. Resource pattern</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground text-sm leading-relaxed">
            Glob over resource identifiers. <code>app:prod-*</code> matches
            apps whose slug starts with <code>prod-</code>;{" "}
            <code>project:billing/*</code> matches everything in the billing
            project.
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">5. Actor pattern (JSON)</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground flex flex-col gap-3 text-sm leading-relaxed">
            <span>
              Who the policy applies to. Match by user ID, role slug, or
              both. Empty <code>{`{}`}</code> matches every actor.
            </span>
            <pre className="bg-muted overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`{ "role_slug": ["senior-eng"] }
{ "user_id": ["usr_01HW…"] }
{ "role_slug": ["contractor"], "team_slug": ["payments"] }`}</code>
            </pre>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">6. Conditions (JSON)</CardTitle>
          </CardHeader>
          <CardContent className="text-muted-foreground flex flex-col gap-3 text-sm leading-relaxed">
            <span>
              Extra predicates evaluated at request time. Available keys:{" "}
              <code>time_of_day</code>, <code>day_of_week</code>,{" "}
              <code>ip_cidr</code>, <code>mfa_max_age_seconds</code>,{" "}
              <code>env</code>.
            </span>
            <pre className="bg-muted overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`{
  "time_of_day": { "after": "22:00", "before": "06:00", "tz": "UTC" },
  "ip_cidr": ["10.0.0.0/8"]
}`}</code>
            </pre>
          </CardContent>
        </Card>
      </div>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Example: prod is senior-only</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Only members of the <code>senior-eng</code> role can deploy any app
          whose slug starts with <code>prod-</code>.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`Scope:    ORG
Effect:   DENY
Action:   app.deploy
Resource: app:prod-*
Actor:    {}             # applies to all actors
Conditions:
  { "role_slug_not_in": ["senior-eng"] }`}</code>
        </pre>
        <p className="text-muted-foreground text-sm leading-relaxed">
          A senior engineer&apos;s deploy passes the role check{" "}
          (<code>role_slug_not_in</code> returns false → policy does not
          match → no deny). Anyone else trips the deny and the deploy fails
          with a permission error.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Example: nightly secret freeze</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Nobody can write secrets between 22:00 and 06:00 UTC — used during
          incident windows or change-freeze periods.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`Scope:    ORG
Effect:   DENY
Action:   secret.write
Resource: *
Actor:    {}
Conditions:
  { "time_of_day": { "after": "22:00", "before": "06:00", "tz": "UTC" } }`}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              Policy is not taking effect.
            </strong>{" "}
            DENY always wins. Check for a broader-scope DENY (ORG-level) that
            already covers the request. The policies table shows the matching
            order; sort by scope.
          </li>
          <li>
            <strong className="text-foreground">
              A user sees a button they shouldn&apos;t.
            </strong>{" "}
            The UI hides controls based on RBAC, not ABAC — the button stays
            visible because the role technically grants the action, but the
            request fails at the resolver when ABAC denies. Either tighten
            the role binding, or surface the ABAC outcome in the UI via{" "}
            <Link
              href="/settings/permissions"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Permissions diagnostic
            </Link>
            .
          </li>
          <li>
            <strong className="text-foreground">
              Conditions JSON not parsing.
            </strong>{" "}
            The dialog validates on save and returns a precise error
            (&ldquo;unknown key <code>time_of_the_day</code>, did you mean{" "}
            <code>time_of_day</code>?&rdquo;). Re-open the policy to see the
            stored value.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/identity-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Identity providers
            </Link>{" "}
            — who can sign in. Policies decide what they can do once they
            have.
          </li>
          <li>
            <Link
              href="/settings/permissions"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Permissions diagnostic
            </Link>{" "}
            — debug a specific user&apos;s effective permissions on a
            specific resource.
          </li>
        </ul>
      </section>
    </article>
  );
}
