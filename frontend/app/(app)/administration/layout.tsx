import { AdministrationShell } from "@/components/screens/administration/organization/AdministrationShell";

import { AdministrationSubnav } from "./administration-subnav";

export const metadata = {
  title: "Administration · Astrolift",
};

export default function AdministrationLayout({ children }: { children: React.ReactNode }) {
  return <AdministrationShell subnav={<AdministrationSubnav />}>{children}</AdministrationShell>;
}
