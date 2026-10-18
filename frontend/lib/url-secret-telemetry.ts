// A URL in a Sentry event keeps no credential it carries (#2440): the value
// of a `code`, `token` or `*_token` query parameter, and the token of an
// invitation link, raw or percent-encoded once or twice inside a `next`.
// The same patterns as the backend's request-log redaction
// (backend/core/logging.py, #2429).
const SEP = "(?:/|%2F|%252F)";
const URL_SECRET_PARAM =
  /((?:[?&;#]|%3F|%26|%3B|%23|%253F|%2526|%253B|%2523)(?:code|token|[a-z0-9_.-]*_token)(?:=|%3D|%253D))[^&;#\s"'<>]*/gi;
const INVITATION_TOKEN = new RegExp(`(${SEP}auth${SEP}invitation${SEP})[A-Za-z0-9_-]+`, "gi");
const SECRET_PARAM_NAME = /^(?:code|token|[a-z0-9_.-]*_token)$/i;
const REDACTED = "***REDACTED***";

export function redactUrlSecrets(text: string): string {
  return text.replace(URL_SECRET_PARAM, `$1${REDACTED}`).replace(INVITATION_TOKEN, `$1${REDACTED}`);
}

function scrubQuery(query: unknown): unknown {
  // A request's query_string: a string without its "?", an object, or [name, value] pairs.
  if (typeof query === "string") return redactUrlSecrets(`?${query}`).slice(1);
  const secret = (name: unknown, value: unknown) =>
    typeof name === "string" && SECRET_PARAM_NAME.test(name) ? REDACTED : value;
  if (Array.isArray(query)) {
    return query.map((pair) => (Array.isArray(pair) ? [pair[0], secret(pair[0], pair[1])] : pair));
  }
  if (query !== null && typeof query === "object") {
    return Object.fromEntries(
      Object.entries(query).map(([name, value]) => [name, secret(name, value)])
    );
  }
  return query;
}

function scrub(value: unknown, visited: WeakSet<object>): unknown {
  if (typeof value === "string") return redactUrlSecrets(value);
  if (value === null || typeof value !== "object" || visited.has(value)) return value;
  visited.add(value);
  const record = value as Record<string, unknown>;
  for (const name of Object.keys(record)) {
    try {
      record[name] =
        name === "query_string" ? scrubQuery(record[name]) : scrub(record[name], visited);
    } catch {
      // A frozen or getter-only property is left as the SDK built it.
    }
  }
  return value;
}

/** `record` with every URL credential in it replaced, in place. For beforeSend and beforeSendTransaction. */
export function scrubUrlSecrets<T>(record: T): T {
  return scrub(record, new WeakSet()) as T;
}
