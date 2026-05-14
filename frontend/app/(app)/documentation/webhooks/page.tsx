import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export const metadata = {
  title: "Webhooks · Documentation · Astrolift",
};

const pythonSample = `import hmac, hashlib

SECRET = b"<your webhook secret>"

def verify(request_body: bytes, header_signature: str) -> bool:
    expected = hmac.new(SECRET, request_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_signature)

# In a Flask / Django / FastAPI handler:
# 1. Read the raw body BEFORE any JSON parsing
# 2. Pull the signature from the X-Astrolift-Signature header
# 3. Reject the request with 401 if verify() returns False`;

const nodeSample = `import crypto from "node:crypto";

const SECRET = process.env.ASTROLIFT_WEBHOOK_SECRET!;

export function verify(rawBody: Buffer, headerSignature: string): boolean {
  const expected = crypto
    .createHmac("sha256", SECRET)
    .update(rawBody)
    .digest("hex");
  // timingSafeEqual to dodge timing-attack leaks
  const a = Buffer.from(expected, "hex");
  const b = Buffer.from(headerSignature, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// Express: use express.raw({ type: "*/*" }) on the route so req.body
// is a Buffer of the unparsed payload. JSON.parse(req.body.toString())
// only AFTER you've verified.`;

const goSample = `package webhook

import (
    "crypto/hmac"
    "crypto/sha256"
    "encoding/hex"
)

func Verify(rawBody []byte, headerSignature string, secret []byte) bool {
    mac := hmac.New(sha256.New, secret)
    mac.Write(rawBody)
    expected := hex.EncodeToString(mac.Sum(nil))
    return hmac.Equal([]byte(expected), []byte(headerSignature))
}

// In your http.Handler:
// body, _ := io.ReadAll(r.Body) // read raw, don't json.Decode first
// if !Verify(body, r.Header.Get("X-Astrolift-Signature"), secret) {
//     http.Error(w, "invalid signature", http.StatusUnauthorized)
//     return
// }`;

export default function WebhooksDocPage() {
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Webhooks</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Subscribe external systems to Astrolift events: Slack relays,
          PagerDuty, status pages, or your own HTTP endpoint.
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">When you need this</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Anything you want to <em>push</em> out of Astrolift in near-real
          time: deploy success or failure into the team Slack, alert fires
          into PagerDuty, deploys into your audit log, preview URLs into
          a documentation board. If you instead want to <em>pull</em>{" "}
          state, use the GraphQL API.
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Prerequisites</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">Permission.</strong>{" "}
            <code>webhook.create</code> (Owner, Admin, and Operator have it).
          </li>
          <li>
            <strong className="text-foreground">
              A target URL Astrolift can reach.
            </strong>{" "}
            Astrolift POSTs to it from the control plane; the URL has to be
            resolvable from there. Localhost / private IPs only work for
            installs that share a network with the receiver.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Create a subscription</h2>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">
            Step 1 — Open the Webhooks page
          </h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Go to{" "}
            <Link
              href="/webhooks"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Webhooks
            </Link>{" "}
            (the org-wide list) or, for app-scoped events, open the{" "}
            <strong>Webhooks</strong> tab on any app.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">
            Step 2 — Enter the target URL
          </h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Paste the HTTPS endpoint that will receive deliveries. The
            scheme must be <code>https://</code> for non-private hosts —
            plaintext is allowed only for internal-network targets.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">
            Step 3 — Select event types
          </h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            Either pick from the suggestion list or type your own. One per
            line, or comma/space separated. <code>*</code> matches every
            event Astrolift emits.
          </p>
          <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
            <code>{`APP_REGISTERED
DEPLOY_STARTED
DEPLOY_SUCCEEDED
DEPLOY_FAILED
PREVIEW_CREATED
PREVIEW_TORN_DOWN
ALERT_FIRED`}</code>
          </pre>
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">
            Step 4 — Save the signing secret
          </h3>
          <p className="text-muted-foreground text-sm leading-relaxed">
            On save, Astrolift shows the HMAC signing secret{" "}
            <strong>exactly once</strong>. The server stores only the
            SHA-256 of the secret — if you lose it, you have to rotate the
            subscription. Copy it into your receiver&apos;s env (e.g.{" "}
            <code>ASTROLIFT_WEBHOOK_SECRET</code>) before dismissing the
            banner.
          </p>
        </div>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Signature verification</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Every delivery includes an{" "}
          <Badge variant="outline" className="font-mono">
            X-Astrolift-Signature
          </Badge>{" "}
          header — a hex-encoded HMAC-SHA256 of the raw request body, keyed
          with the subscription secret. Verify before you parse anything.
        </p>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <strong className="text-foreground">
              Read the body raw, before any JSON parsing.
            </strong>{" "}
            Whitespace and key ordering matter — re-serialising the parsed
            JSON will not reproduce the same signature.
          </li>
          <li>
            <strong className="text-foreground">Use constant-time compare.</strong>{" "}
            <code>hmac.compare_digest</code> in Python,{" "}
            <code>crypto.timingSafeEqual</code> in Node,{" "}
            <code>hmac.Equal</code> in Go. Plain <code>==</code> leaks
            timing.
          </li>
          <li>
            <strong className="text-foreground">
              Reject on mismatch with HTTP 401.
            </strong>{" "}
            Astrolift counts non-2xx responses and disables the subscription
            after 50 consecutive failures.
          </li>
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">Python</h3>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{pythonSample}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">Node.js / TypeScript</h3>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{nodeSample}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="text-base font-medium">Go</h3>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{goSample}</code>
        </pre>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Troubleshooting</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">
              Subscription was auto-disabled.
            </strong>{" "}
            Too many 5xx responses in a row (the threshold is 50 consecutive
            failures). Open the subscription, fix the receiver, and click{" "}
            <strong>Re-enable</strong>. The{" "}
            <code>astrolift_workflows.periodic_maintenance</code> Workflow
            also re-heals stuck subscriptions on its next tick.
          </li>
          <li>
            <strong className="text-foreground">Signature mismatch on every call.</strong>{" "}
            The body is being read post-parse. In Express, use{" "}
            <code>express.raw({"{ type: \"*/*\" }"})</code> on the route. In
            FastAPI, read <code>await request.body()</code> instead of{" "}
            <code>await request.json()</code>. In Go, copy the body before
            passing it to any decoder.
          </li>
          <li>
            <strong className="text-foreground">
              Some events never arrive.
            </strong>{" "}
            The subscription only fires for events in its event list.{" "}
            <code>*</code> matches everything; otherwise the slug has to be
            exact. The latest deliveries table on the subscription page
            shows what Astrolift attempted, with response status and body.
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/webhook-events"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Webhook events
            </Link>{" "}
            — payload schema and JSON examples for every event you can
            subscribe to.
          </li>
          <li>
            <Link
              href="/documentation/source-providers"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Source providers
            </Link>{" "}
            — incoming SCM webhooks (Astrolift as the receiver, not the
            sender).
          </li>
          <li>
            <Link
              href="/documentation/policies"
              className="text-foreground underline-offset-2 hover:underline"
            >
              ABAC policies
            </Link>{" "}
            — restrict who can create or delete webhook subscriptions.
          </li>
        </ul>
      </section>
    </article>
  );
}
