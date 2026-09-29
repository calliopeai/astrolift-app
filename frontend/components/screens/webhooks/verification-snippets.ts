/**
 * Receiver-side HMAC verification snippets surfaced from the
 * "Show verification snippet" disclosure on the webhooks page
 * (#426). Each snippet implements the same shape:
 *
 *   1. Read the X-Astrolift-Signature, X-Astrolift-Timestamp, and
 *      raw request body.
 *   2. Recompute `HMAC-SHA256(secret, "<timestamp>.<raw_body>")`.
 *   3. Constant-time compare against the presented signature.
 *   4. Reject when |now - timestamp| > 300s (replay protection).
 *
 * Kept inline (not in i18n) — these are code blocks copy-pasted
 * into the receiver's repo, not translated UI strings.
 */

export type SnippetLanguage = "node" | "python" | "go";

export const SNIPPET_LABELS: Record<SnippetLanguage, string> = {
  node: "Node.js",
  python: "Python",
  go: "Go",
};

export const SNIPPETS: Record<SnippetLanguage, string> = {
  node: `// Node 18+ — works as an Express handler.
import crypto from "node:crypto";

const SECRET = process.env.ASTROLIFT_WEBHOOK_SECRET; // your subscription secret
const FRESHNESS_WINDOW_SECONDS = 300;

export function verifyAstroliftWebhook(req, rawBody /* Buffer */) {
  const presented = req.header("x-astrolift-signature");
  const timestamp = Number(req.header("x-astrolift-timestamp"));
  if (!presented || !timestamp) return false;

  // Replay protection: reject deliveries older than the window.
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - timestamp) > FRESHNESS_WINDOW_SECONDS) return false;

  const signingInput = Buffer.concat([
    Buffer.from(\`\${timestamp}.\`, "ascii"),
    rawBody,
  ]);
  const expected =
    "sha256=" +
    crypto.createHmac("sha256", SECRET).update(signingInput).digest("hex");

  // Constant-time compare.
  const a = Buffer.from(expected);
  const b = Buffer.from(presented);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}
`,
  python: `# Python 3.10+
import hmac, hashlib, os, time

SECRET = os.environ["ASTROLIFT_WEBHOOK_SECRET"].encode()
FRESHNESS_WINDOW_SECONDS = 300

def verify_astrolift_webhook(headers, raw_body: bytes) -> bool:
    presented = headers.get("X-Astrolift-Signature", "")
    timestamp_raw = headers.get("X-Astrolift-Timestamp", "")
    if not presented or not timestamp_raw:
        return False
    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return False

    # Replay protection.
    if abs(int(time.time()) - timestamp) > FRESHNESS_WINDOW_SECONDS:
        return False

    signing_input = f"{timestamp}.".encode("ascii") + raw_body
    digest = hmac.new(SECRET, signing_input, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, presented)
`,
  go: `// Go 1.21+
package webhook

import (
\t"crypto/hmac"
\t"crypto/sha256"
\t"encoding/hex"
\t"fmt"
\t"net/http"
\t"os"
\t"strconv"
\t"time"
)

const FreshnessWindowSeconds = 300

func VerifyAstroliftWebhook(r *http.Request, rawBody []byte) bool {
\tsecret := []byte(os.Getenv("ASTROLIFT_WEBHOOK_SECRET"))
\tpresented := r.Header.Get("X-Astrolift-Signature")
\ttsRaw := r.Header.Get("X-Astrolift-Timestamp")
\tif presented == "" || tsRaw == "" {
\t\treturn false
\t}
\tts, err := strconv.ParseInt(tsRaw, 10, 64)
\tif err != nil {
\t\treturn false
\t}

\t// Replay protection.
\tdelta := time.Now().Unix() - ts
\tif delta < 0 {
\t\tdelta = -delta
\t}
\tif delta > FreshnessWindowSeconds {
\t\treturn false
\t}

\tmac := hmac.New(sha256.New, secret)
\tmac.Write([]byte(fmt.Sprintf("%d.", ts)))
\tmac.Write(rawBody)
\texpected := "sha256=" + hex.EncodeToString(mac.Sum(nil))

\treturn hmac.Equal([]byte(expected), []byte(presented))
}
`,
};
