import { CheckIcon, MinusIcon, PlugIcon } from "lucide-react";
import Link from "next/link";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { CAPABILITIES, type useDrivers } from "./use-drivers";

export type DriversScreenProps = ReturnType<typeof useDrivers>;

export function DriversScreen({
  loading,
  rows,
  clusterCountByProvider,
  managedKindColumns,
  usingFallback,
}: DriversScreenProps) {
  return (
    <PageShell
      title="Driver reference"
      description="Provider plugins and the per-capability driver implementations they advertise. The matrix below is sourced from the registered ProviderPlugin manifests; when no plugins are live, a static known-provider list is shown."
    >
      {loading ? (
        <Card>
          <CardContent className="p-6">
            <Skeleton className="h-44 w-full" />
          </CardContent>
        </Card>
      ) : (
        <>
          {usingFallback && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Static reference</CardTitle>
                <CardDescription>
                  No provider plugins are registered on this install yet. The table below shows the
                  canonical capability surface for each supported provider. Once plugins load, this
                  surface switches to the live manifest.
                </CardDescription>
              </CardHeader>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <PlugIcon className="size-4" />
                Capability matrix
              </CardTitle>
              <CardDescription>
                Each row is a provider; columns are the driver kinds it bundles. A check means the
                provider implements that driver.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[260px]">Provider</TableHead>
                    {CAPABILITIES.map((cap) => (
                      <TableHead key={cap.key} className="text-center">
                        {cap.label}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => {
                    const boundCount = clusterCountByProvider.get(row.slug) ?? 0;
                    return (
                      <TableRow key={row.slug}>
                        <TableCell>
                          <div className="flex flex-col gap-1">
                            <span className="text-sm font-medium">{row.name}</span>
                            <span className="text-muted-foreground font-mono text-xs">
                              {row.slug}
                              {row.version ? ` · v${row.version}` : ""}
                            </span>
                            {boundCount > 0 ? (
                              <Link href="/clusters" className="inline-flex w-fit">
                                <Badge variant="secondary" className="text-2xs">
                                  {boundCount}{" "}
                                  {boundCount === 1 ? "cluster bound" : "clusters bound"}
                                </Badge>
                              </Link>
                            ) : (
                              <span className="text-muted-foreground text-2xs">No clusters</span>
                            )}
                          </div>
                        </TableCell>
                        {CAPABILITIES.map((cap) => (
                          <TableCell key={cap.key} className="text-center">
                            {row.capabilities[cap.key] ? (
                              <CheckIcon
                                className="text-success-fg mx-auto size-4"
                                aria-label="supported"
                              />
                            ) : (
                              <MinusIcon
                                className="text-muted-foreground mx-auto size-4"
                                aria-label="not supported"
                              />
                            )}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              <p className="text-muted-foreground mt-4 text-xs">
                Matrix reflects manifest declarations · last probed varies by cluster ·{" "}
                <Link href="/clusters" className="text-primary hover:underline">
                  See live capabilities →
                </Link>
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Managed services per provider</CardTitle>
              <CardDescription>
                Which managed-service kinds each provider can provision. Bindings declared in{" "}
                <code>[[managed_services]]</code> on the app manifest must match one of the kinds
                listed here.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[200px]">Provider</TableHead>
                    {managedKindColumns.map((kind) => (
                      <TableHead key={kind} className="text-center">
                        {kind}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.slug}>
                      <TableCell>
                        <span className="text-sm font-medium">{row.name}</span>
                      </TableCell>
                      {managedKindColumns.map((kind) => (
                        <TableCell key={kind} className="text-center">
                          {row.managedServices.includes(kind) ? (
                            <Badge className="text-2xs">yes</Badge>
                          ) : (
                            <span className="text-muted-foreground text-xs">—</span>
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </PageShell>
  );
}
