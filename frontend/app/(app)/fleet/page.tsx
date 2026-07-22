import { redirect } from "next/navigation";

// /fleet is a section, not a destination — land on the fleet map (#1091).
export default function FleetPage() {
  redirect("/fleet/map");
}
