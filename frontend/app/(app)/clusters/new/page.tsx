import { RegisterClusterClient } from "./register-cluster-client";

export const metadata = { title: "Register cluster · Astrolift" };

/**
 * Register cluster: a page, not a sheet, since it asks for more than three
 * fields (spec 44 §5.4). A static segment, so it wins over `[slug]`.
 */
export default function RegisterClusterRoute() {
  return <RegisterClusterClient />;
}
