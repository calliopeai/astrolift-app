import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export const metadata = {
  title: "Custom domains · Documentation · Astrolift",
};

export default function CustomDomainsPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Custom domains</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Bring your own hostname (for example <code>app.acme.com</code>) and let
          Astrolift handle TLS via cert-manager.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">When you need this</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Every app gets a generated <code>*.astrolift.app</code> subdomain by
          default. You only need this guide when you want a branded hostname
          like <code>app.acme.com</code> or <code>checkout.acme.com</code> in
          front of your workload — with automatic TLS managed by cert-manager
          inside the tenant cluster.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Prerequisites</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">A connected cluster.</strong>{" "}
            The app is deployed to a tenant cluster that has cert-manager and
            the ingress controller installed (this is the default for clusters
            provisioned by opscode).
          </li>
          <li>
            <strong className="text-foreground">DNS you can edit.</strong> You
            need access to the DNS provider that holds the parent zone so you
            can add a CNAME (or TXT) record.
          </li>
          <li>
            <strong className="text-foreground">Permission.</strong> An
            operator role that includes <code>app.update</code> — Owner, Admin,
            and Deployer have this by default.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Step 1 — Open the Domains tab</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Navigate to your app, then click the <strong>Domains</strong> tab.
          You will see any hostnames already bound to the app and a button to
          add a new one.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`/apps/<your-app>/domains  →  Add domain`}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">
          Step 2 — Enter the FQDN and validation method
        </h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Type the full hostname. The validation method defaults to{" "}
          <Badge variant="outline" className="mx-0.5 font-mono">
            dns-01
          </Badge>{" "}
          which works for both apex and wildcard records and does not require
          the hostname to be publicly reachable before issuance. Leave it
          blank to accept the platform default.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`Hostname:           app.acme.com
Validation method:  dns-01   (default)`}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">
          Step 3 — Astrolift returns the record to publish
        </h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          After you save, the row appears with a{" "}
          <Badge variant="secondary">pending_validation</Badge> cert state and
          a validation token. For <code>dns-01</code> this is a TXT record at{" "}
          <code>_acme-challenge.app.acme.com</code>; for{" "}
          <code>http-01</code> it is a CNAME pointing at the cluster ingress.
          Click the token to copy it.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`# dns-01
Type:   TXT
Name:   _acme-challenge.app.acme.com
Value:  <token from the Domains tab>
TTL:    300

# http-01
Type:   CNAME
Name:   app.acme.com
Value:  ingress.<cluster>.astrolift.app`}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">
          Step 4 — Publish the record at your DNS provider
        </h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Add the record verbatim. Most providers propagate within a minute
          or two, but TTLs are advisory — give it up to ~10 minutes before
          you escalate to troubleshooting.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Step 5 — Wait for activation</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Astrolift polls every ~30 seconds. Once the record is visible from
          its resolvers, cert-manager issues the certificate and the row
          transitions{" "}
          <Badge variant="secondary">pending_validation</Badge>{" "}
          →{" "}
          <Badge variant="secondary">validated</Badge>{" "}
          →{" "}
          <Badge
            className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
            variant="secondary"
          >
            active
          </Badge>
          . You can force an immediate poll with the <strong>Recheck</strong>{" "}
          button on the row.
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              Cert stays in pending_validation.
            </strong>{" "}
            DNS likely has not propagated to the issuing resolvers. Check from
            an outside network:
            <pre className="bg-muted mt-2 overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`dig +short TXT _acme-challenge.app.acme.com
# expected: the validation token from the Domains tab`}</code>
            </pre>
          </li>
          <li>
            <strong className="text-foreground">Validation failing.</strong>{" "}
            The TXT record value must match the token verbatim — no surrounding
            quotes, no trailing whitespace, no smart-quoted characters
            (copy/paste from a terminal, not from a doc).
          </li>
          <li>
            <strong className="text-foreground">CAA blocks issuance.</strong>{" "}
            If the parent zone has a CAA record that does not list{" "}
            <code>letsencrypt.org</code>, ACME cannot issue. Add a CAA entry
            allowing Let&apos;s Encrypt, or remove the restrictive record.
          </li>
          <li>
            <strong className="text-foreground">
              Cert expired and was not rotated.
            </strong>{" "}
            cert-manager auto-renews ~30 days before expiry. If it has not,
            an operator can inspect the cert-manager logs in the tenant
            cluster — the rotation Workflow re-heals on the next periodic
            tick.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/source-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Source providers
            </Link>{" "}
            — connect GitHub or GitLab so push triggers deploys to your app.
          </li>
          <li>
            <Link
              href="/documentation/identity-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Identity providers
            </Link>{" "}
            — register the SSO domains your users sign in from.
          </li>
        </ul>
      </section>
    </article>
  );
}
