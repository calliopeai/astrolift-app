import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { Group, Source } from "./configuration-groups";

function sourceBadge(source: Source) {
  switch (source) {
    case "image":
      return <Badge variant="outline">image</Badge>;
    case "platform":
      return <Badge variant="secondary">platform-injected</Badge>;
    case "operator":
    default:
      return <Badge variant="outline">operator</Badge>;
  }
}

export interface ConfigurationScreenProps {
  groups: Group[];
}

/** The static "Configuration" documentation page: one env-var table per group. */
export function ConfigurationScreen({ groups }: ConfigurationScreenProps) {
  return (
    <article className="flex max-w-4xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Configuration</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Every environment variable Astrolift reads at runtime. Source ={" "}
          <Badge variant="outline" className="mx-0.5">
            operator
          </Badge>{" "}
          means the operator sets it,{" "}
          <Badge variant="outline" className="mx-0.5">
            image
          </Badge>{" "}
          means the container image bakes a default in, and{" "}
          <Badge variant="secondary" className="mx-0.5">
            platform-injected
          </Badge>{" "}
          means Astrolift writes it into the workload at deploy time.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Conventions</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            Boolean values accept <code>true / false / 1 / 0 / yes / no / on / off</code>{" "}
            (case-insensitive).
          </li>
          <li>
            Vars marked <strong className="text-foreground">required</strong> either have no default
            or have a default that&apos;s only safe for local development (e.g.{" "}
            <code>not-a-secret</code>).
          </li>
          <li>Anything not listed here is not read by Astrolift — silently ignored if set.</li>
        </ul>
      </section>

      <nav className="flex flex-col gap-2">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
          On this page
        </h2>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {groups.map((g) => (
            <li key={g.id}>
              <a href={`#${g.id}`} className="text-foreground underline-offset-2 hover:underline">
                {g.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      {groups.map((group) => (
        <section key={group.id} id={group.id} className="flex flex-col gap-3">
          <h2 className="text-lg font-medium">{group.title}</h2>
          <p className="text-muted-foreground text-sm leading-relaxed">{group.intro}</p>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-1/4">Variable</TableHead>
                  <TableHead>Purpose</TableHead>
                  <TableHead className="w-32">Default</TableHead>
                  <TableHead className="w-20">Required</TableHead>
                  <TableHead className="w-32">Source</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.vars.map((v) => (
                  <TableRow key={v.name}>
                    <TableCell className="align-top font-mono text-xs">{v.name}</TableCell>
                    <TableCell className="text-muted-foreground align-top text-xs leading-relaxed">
                      {v.purpose}
                    </TableCell>
                    <TableCell className="text-muted-foreground align-top font-mono text-xs">
                      {v.default ?? "—"}
                    </TableCell>
                    <TableCell className="align-top">
                      {v.required ? (
                        <Badge className="bg-warning/15 text-warning-fg" variant="secondary">
                          yes
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">no</span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">{sourceBadge(v.source)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      ))}

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">How to inspect what&apos;s set</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          The Django management command <code>features</code> prints the current feature-flag state,
          and <code>python manage.py diffsettings</code> shows every Django setting against its
          default. The control plane never echoes secret values — settings marked sensitive print as{" "}
          <code>***</code>.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`./run.sh manage features
./run.sh manage diffsettings`}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/get-started"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Get started
            </Link>{" "}
            — bring the stack up locally; env vars there default to the docker-compose service
            names.
          </li>
          <li>
            <Link
              href="/documentation/identity-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Identity providers
            </Link>{" "}
            — the AUTH0_* / ASTROLIFT_IDP_* vars are described in detail.
          </li>
          <li>
            <Link
              href="/documentation/cluster-prerequisites"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Cluster prerequisites
            </Link>{" "}
            — what the tenant cluster needs separately from these env vars.
          </li>
        </ul>
      </section>
    </article>
  );
}
