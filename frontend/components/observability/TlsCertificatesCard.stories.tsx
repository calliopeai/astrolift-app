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

const MANY_CERTS = Array.from({ length: 30 }, (_, i) => ({
  id: `cert-${i}`,
  hostname: `svc-${i}.checkout.astro.example.com`,
  issuer: "Amazon RSA 2048 M02",
  notAfter: "2027-01-01T00:00:00Z",
  daysUntilExpiry: i * 7,
  renewalStatus: i % 5 === 0 ? "failed" : "auto",
}));

/** Thirty certificates: the embedded list pages them, soonest to expire first. */
export const ManyCertificates: StoryObj = {
  render: () => <TlsCertificatesCard {...BASE} data={data(MANY_CERTS as never)} />,
};

export const Loading: StoryObj = {
  render: () => <TlsCertificatesCard {...BASE} data={null} loading />,
};

export const LongStrings: StoryObj = {
  render: () => (
    <TlsCertificatesCard
      {...BASE}
      data={data([
        {
          id: "cert-long",
          hostname: `${"a".repeat(60)}.checkout.astro.example.com`,
          issuer: `CN=${"b".repeat(200)}`,
          notAfter: "2026-10-01T00:00:00Z",
          daysUntilExpiry: 3,
          renewalStatus: "manual",
        },
      ] as never)}
    />
  ),
};

export const Width768: StoryObj = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <TlsCertificatesCard {...BASE} data={data(MANY_CERTS as never)} />
    </div>
  ),
};
