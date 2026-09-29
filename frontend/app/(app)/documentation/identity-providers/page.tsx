import { IDENTITY_PROVIDER_KINDS } from "@/components/screens/documentation/identity-provider-kinds";
import { IdentityProvidersDoc } from "@/components/screens/documentation/IdentityProvidersDoc";

export const metadata = {
  title: "Identity providers · Documentation · Astrolift",
};

export default function IdentityProvidersDocPage() {
  return <IdentityProvidersDoc kinds={IDENTITY_PROVIDER_KINDS} />;
}
