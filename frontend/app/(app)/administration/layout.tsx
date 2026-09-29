export const metadata = {
  title: "Administration · Astrolift",
};

// Each Admin page draws its own `Admin ▾ › <function>` header (spec 44 §4.4);
// the rail and that switcher replace the old Administration tab strip.
export default function AdministrationLayout({ children }: { children: React.ReactNode }) {
  return children;
}
