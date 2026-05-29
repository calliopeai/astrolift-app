import { AdministrationSubnav } from "./administration-subnav";

export const metadata = {
  title: "Administration · Astrolift",
};

export default function AdministrationLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-1 flex-col">
      <AdministrationSubnav />
      <div className="flex-1">{children}</div>
    </div>
  );
}
