/** Only resource identity fields enter a confirmation summary, never scripts or credentials. */
export function nativeTarget(text: string): string | null {
  try {
    const object = (value: unknown): Record<string, unknown> =>
      value !== null && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
    const input = object(JSON.parse(text));
    const path = object(input.path),
      body = object(input.body);
    const document = object(input.document),
      metadata = object(document.metadata),
      publicMetadata = object(body.metadata);
    const identities = [
      path.clusterID,
      path.nodePoolID,
      path.nodeID,
      path.name,
      body.clusterID,
      input.name,
      metadata.name,
      publicMetadata.name,
      body.name,
    ].filter(
      (value): value is string =>
        typeof value === "string" && /^[a-z0-9][a-z0-9.-]{0,252}$/i.test(value)
    );
    return [...new Set(identities)].join(" / ") || null;
  } catch {
    return null;
  }
}
