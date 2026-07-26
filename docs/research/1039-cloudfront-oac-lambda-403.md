# #1039 — CloudFront OAC to Lambda Function URL returns 403: root-cause analysis

Status: research / parked. The shipped public-faas invoke path is API Gateway
HTTP API (#987/#1035); this document exists so the OAC-fronted-Lambda path can
be revived deliberately later. No production code changes accompany it.

## TL;DR

- The `403 {"Message":"Forbidden. For troubleshooting Function URL
  authorization issues..."}` from an `AuthType=AWS_IAM` Lambda Function URL is
  **deliberately ambiguous**: the Function URL IAM authorizer returns the *same*
  response for an invalid SigV4 signature and for a valid signature that is not
  authorized. Config inspection therefore **cannot** distinguish the two, which
  is exactly why a textbook-correct config still 403s and why every
  config-iteration attempt has failed. This is a diagnosis problem, not (yet) a
  config problem.
- **Most likely root cause: the SigV4 request signing, not the resource
  policy.** The authorization side of the config (principal, action,
  `AWS:SourceArn`, `lambda:FunctionUrlAuthType`) has been verified textbook and
  matches AWS's reference; the classic authorization killers (Host-header
  forwarding, `SourceArn` mismatch, public-URL block) are already ruled out. A
  bare `GET /` with no body also fails, which rules out the well-known "OAC does
  not sign the request body" POST/PUT limitation. What remains most probable is a
  canonical-request mismatch between what CloudFront's OAC signs and what the
  Function URL recomputes — OAC-for-Lambda is a recent (2024) AWS feature with
  documented signing edge cases.
- **One decisive next test bisects it with a single one-line policy change (no
  logs, no AWS support):** temporarily grant `cloudfront.amazonaws.com`
  `lambda:InvokeFunctionUrl` with **no `Condition` block at all**.
  - Still 403 -> the principal/signature is not even being recognized ->
    **signing** problem. Move to request-level tracing.
  - Now 200 -> a **condition** was silently failing to match -> authorization
    problem; re-add conditions one at a time to find which (`AWS:SourceArn` is
    the prime suspect).

## How OAC -> Lambda Function URL auth actually works

1. Viewer hits the CloudFront distribution.
2. CloudFront, because the origin has an OAC with
   `OriginAccessControlOriginType=lambda`, `SigningProtocol=sigv4`,
   `SigningBehavior=always`, signs the **origin** request with SigV4 as the
   `cloudfront.amazonaws.com` service principal, scoped to the `lambda` service
   in the Function URL's region (region derived from the origin domain
   `<id>.lambda-url.<region>.on.aws`).
3. The Function URL (`AuthType=AWS_IAM`) validates the SigV4 signature, resolves
   the caller to the CloudFront service principal, then evaluates the Lambda
   **resource-based policy** for an `Allow` of `lambda:InvokeFunctionUrl`.
4. Two independent gates must both pass: **(a) the signature is valid**, and
   **(b) IAM authorizes the resolved principal**. A failure of *either* yields
   the identical `403 Forbidden` body. There is no public signal telling you
   which gate failed — this is intentional (avoids leaking auth internals).

Because SCPs apply to IAM principals **in your account**, not to AWS service
principals, an account SCP does not evaluate against `cloudfront.amazonaws.com`
on this path. So the "account blocks public Function URLs" guardrail (an SCP on
`AuthType=NONE`) is unlikely to be the cause of the OAC 403 — it constrains the
URL's AuthType, which is already `AWS_IAM` here. (See "Lower-probability"
below for the residual account-level angle worth a quick check.)

## What the verified config already rules out

From the issue (all verified live on prd, dist `E3HVY7N6O0E8CD`):

| Candidate | Status | Why it's out |
| --- | --- | --- |
| Public Function URL blocked (`AuthType=NONE`) | ruled out | URL is `AWS_IAM`; that is the whole point of the OAC path. |
| OAC not attached | ruled out | `OriginAccessControlId` set on the origin. |
| `SourceArn` mismatch | ruled out (by inspection) | Resource-policy `AWS:SourceArn` matches the live distribution ARN exactly. But see caveat below — "matches by inspection" is not "matches at evaluation." |
| Viewer `Host` forwarded to origin | ruled out | `AllViewerExceptHostHeader` managed policy; the Function URL sees its own Host, so Host is not corrupting the signature. |
| Legacy `ForwardedValues` | ruled out | Replaced with managed `CachingDisabled` + `AllViewerExceptHostHeader`. |
| Edge propagation | ruled out | `Deployed`, persists >10 min. |
| **OAC does not sign the request body** (POST/PUT) | **ruled out as the cause of this symptom** | A no-body `GET /` also 403s; the body-signing limitation only affects requests that carry a body. It remains a real constraint to design around *after* GET works. |

## Ranked hypotheses

### 1. (Most likely) SigV4 canonical-request mismatch — a signing failure

Given the authorization side is verified and Host forwarding is excluded, the
highest-probability remaining cause is that the signature CloudFront sends does
not validate at the Function URL. Concrete sub-causes, in order:

- **Payload-hash handling (`x-amz-content-sha256`).** OAC-for-Lambda's
  signing of the payload hash is the least-mature part of the feature. Even for
  a GET, the canonical request includes a content hash; a mismatch here fails
  validation for every method.
- **A header present on the wire but not in the signed-headers set (or vice
  versa).** If CloudFront adds/normalizes a header after (or inconsistently
  with) computing `SignedHeaders`, the Function URL's recomputed canonical
  request diverges. This is the "header canonicalization edge" the issue names.
- **Region/service scoping of the credential.** If CloudFront derived the wrong
  region for the `lambda` service (e.g. an origin-domain typo), the credential
  scope is wrong and the signature is rejected. Low, but a 30-second re-check of
  the exact origin domain string is cheap.

Why this ranks first: the error is the generic authorizer Forbidden (covers bad
signatures), every *authorization* knob has been individually verified, the
symptom hits even a trivial GET, and the feature is new enough that signing
edge cases are the live risk.

### 2. (Plausible) A resource-policy condition silently not matching — authorization

"Matches by inspection" is not "matches at evaluation." The prime suspect is
**`AWS:SourceArn`**: for the Allow to fire, CloudFront must actually populate the
`aws:SourceArn` request-context key with the distribution ARN at invoke time,
and it must equal the statement value under `StringEquals`. Any drift (a
stale ARN after a distribution recreate, a region/account-id transcription
difference, or CloudFront not presenting the key on this path) produces a silent
default-deny that looks identical to a signing failure. The
`lambda:FunctionUrlAuthType=AWS_IAM` condition is lower-risk (the URL is
AWS_IAM, so it should match) but is part of the same conditioned statement and
should be bisected out together.

The code that writes this statement is
`backend/providers/aws/managed/faas_lambda.py::allow_cloudfront_invoke`
(`add_permission` with `Principal=cloudfront.amazonaws.com`,
`Action=lambda:InvokeFunctionUrl`, `SourceArn=<dist arn>`,
`FunctionUrlAuthType=AWS_IAM`). Boto3 renders this as a `Condition.StringEquals`
on both `AWS:SourceArn` and `lambda:FunctionUrlAuthType`. The decisive test
(TL;DR) removes exactly this `Condition` block.

### 3. (Lower) Update-vs-create OAC activation quirk

Attaching an OAC to a pre-existing distribution/origin (rather than creating the
distribution with the OAC in place) has anecdotal reports of not fully
activating until recreated. Worth a single clean-room retry (brand-new
distribution + brand-new function) once hypotheses 1-2 are bisected — but do not
lead with it; it is folklore-grade and cannot be confirmed from config.

### Lower-probability account-level angle

SCPs do not gate the CloudFront service principal, so the public-URL guardrail
should not touch this path. The residual check: confirm there is no
**permissions boundary or region-scoped control** on the *function's* resource
policy surface (e.g. a control that strips/limits resource-based grants), and
confirm the Function URL region is one the account permits. Fast to verify while
you have console access; do not invest beyond a glance.

## Concrete recommendation

1. **Bisect signing vs authorization first (cheapest, most decisive).**
   Temporarily replace the conditioned grant with an unconditioned one on a
   scratch function fronted by an identically configured OAC distribution:
   ```
   aws lambda add-permission \
     --function-name <fn> --statement-id oac-nocond-test \
     --action lambda:InvokeFunctionUrl \
     --principal cloudfront.amazonaws.com \
     --function-url-auth-type AWS_IAM
   ```
   (no `--source-arn`). Re-curl the distribution.
   - **Still 403** -> signing problem -> go to step 2.
   - **200** -> a condition was the culprit -> re-add `--source-arn` alone,
     re-test; the failing condition is identified. Fix is a corrected
     `AWS:SourceArn` (or dropping the redundant `FunctionUrlAuthType` condition).
     Do NOT ship the unconditioned grant — it is a test only (any distribution
     could invoke).
2. **If signing: capture the actual request.** Enable CloudFront **real-time
   logs** (standard logs omit the detail needed), issue one request, and inspect
   the SigV4 CloudFront emitted — `Authorization` header (credential scope +
   `SignedHeaders`), `x-amz-date`, and `x-amz-content-sha256`. Compare the
   signed-headers set against the headers actually on the origin request.
   Diverging signed headers or a payload-hash mismatch confirms hypothesis 1.
   If it reproduces cleanly, this is AWS-support-case material (OAC-for-Lambda
   signing internals are not user-tunable).
3. **Clean-room retry** (hypothesis 3) if 1-2 are inconclusive: a brand-new
   distribution created with the OAC in place from the start, pointed at a
   brand-new AWS_IAM Function URL, minimal (unconditioned first, then
   conditioned) grant. If a from-scratch build works where the updated one
   didn't, the fix is procedural (always create with OAC, never attach later).
4. **Design note for whenever GET works: the POST/PUT body-signing limitation
   still applies.** OAC does not sign request bodies for Lambda origins, so
   requests with bodies will 403 even after the GET path is fixed. Plan for a
   body-handling strategy (CloudFront Function, or accept GET/query-only) before
   declaring the OAC path production-ready.

## Code pointers (for revival)

- `backend/providers/aws/managed/faas_lambda.py`
  - `_ensure_function_url` (~L583) — creates the `AuthType=AWS_IAM` Function URL
    and reaps any legacy public-`NONE` grant. This still runs today.
  - `allow_cloudfront_invoke` (~L609) + `_CLOUDFRONT_INVOKE_STATEMENT_ID`
    (~L79) — the CloudFront-scoped invoke grant. **Retained but no longer
    called** (the shipped path is API Gateway). Reviving OAC means re-wiring a
    post-cdn call to this after the `cdn` row's distribution ARN exists.
- `backend/providers/aws/managed/api_gateway_http.py` — the shipped public
  invoke path that replaced this (context for why the OAC path is parked).
- `backend/astrolift_workflows/activities/faas.py::_service_names` /
  `ensure_faas_services` — provisions the `api_gateway` row (not a `cdn` row)
  today; the revival re-introduces the `cdn` row + the post-cdn grant call.

## Confidence

Moderate on the *ranking* (signing > conditioned-authorization > activation
quirk), grounded in AWS's documented auth mechanics and the config already
verified. **Not high on a single definitive root cause** — by design, the
Forbidden response is ambiguous, so a request-level trace (or the step-1
bisection) is required to convert this from ranked hypotheses to a confirmed
cause. That matches the issue's own "needs more than config iteration"
assessment; step 1 above is the cheapest way to make progress without logs.
