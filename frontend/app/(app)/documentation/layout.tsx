"use client";

import { usePathname } from "next/navigation";

import { DocsShell } from "@/components/screens/documentation/DocsShell";

export default function DocumentationLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return <DocsShell pathname={pathname}>{children}</DocsShell>;
}
