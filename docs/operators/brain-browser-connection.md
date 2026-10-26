# Open a hosted brain with the current person's read access

The brain Connect tab's optional Open portal action requires
`NEXT_PUBLIC_BRAIN_BROWSER_CONNECT=1` in the platform UI build and the matching
native browser handoff installed on that brain. Leave it disabled until the
paired platform, edge policy and native wrapper are qualified. Source tests
alone do not establish a deployed working connection.

Configure the wrapper's exact `BRAIN_BROWSER_PLATFORM_ORIGIN`, brain origin and
brain GUID according to its own contract. The platform opens the selected HTTPS
origin at `/__astrolift/brain-browser/login`; the popup completes its normal edge
login and supplies its brain identity and nonce. Only that origin and exact
popup may complete the exchange. The short-lived brain read token travels in
that explicit exchange, never in a URL, localStorage or the Apollo cache.

This action requires the normal platform bearer session. Cookie-only development
login is unavailable for this handoff because the client cannot pin that opaque
credential. The platform reads the current native `astroliftMyProfile.userId`
without a cache before and after token issuance. It also checks the captured
session epoch/credential, platform origin, active organization and selected
brain/endpoint before delivery. Observed logout, session expiry, token or
organization changes cancel the attempt, including a delayed mint result.
Cancellation closes the popup and removes listeners; reconnect requires a new
explicit action. No token is automatically replayed or refreshed.

These client checks prevent disclosure by a stale UI attempt; they do not
authorize a read or provide atomic cross-system logout. The native receiver must
independently verify the token and enforce its native read authority. This path
grants no native mutation, App Metric operation or application service authority.
