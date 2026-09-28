import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  TlsCertificatesCard,
  type TlsCertificatesCardData,
} from "@/components/observability/TlsCertificatesCard";

const meta: Meta = { title: "Patterns/Observability/TlsCertificatesCard" };
export default meta;

const data = (certificates: unknown[], reason = "OK"): TlsCertificatesCardData =>
  ({ astroliftAppCertificates: { reason, certificates } }) as TlsCertificatesCardData;

const BASE = { appSlug: "checkout", loading: false, onRefresh: () => {} };

export const Certificates: StoryObj = {
  render: () => (
    <TlsCertificatesCard
      {...BASE}
      data={data([
        {
          id: "1",
          hostname: "checkout.astro.example.com",
          issuer: "Amazon RSA 2048 M02",
          notAfter: "2026-12-01T00:00:00Z",
          daysUntilExpiry: 64,
          renewalStatus: "eligible",
        },
        {
          id: "2",
          hostname: "*.astro.example.com",
          issuer: "Amazon RSA 2048 M02",
          notAfter: "2026-10-07T00:00:00Z",
          daysUntilExpiry: 9,
          renewalStatus: "pending",
        },
      ])}
    />
  ),
};
export const NotSupported: StoryObj = {
  render: () => <TlsCertificatesCard {...BASE} data={data([], "NOT_SUPPORTED")} />,
};
