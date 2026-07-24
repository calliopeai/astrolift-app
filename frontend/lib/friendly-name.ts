// Friendly-name generator — produces a memorable slug like
// `exciting-talkative-platypus` so a registered/bootstrapped app never has to
// start nameless (the "Top-level app name is required." friction). Words are
// chosen so `adjective-adjective-animal` stays inside the app slug contract
// (`^[a-z0-9-]{1,40}$`); an over-long combo falls back to `adjective-animal`.

const ADJECTIVES = [
  "eager", "brave", "calm", "clever", "bold", "bright", "swift", "quiet",
  "lively", "gentle", "keen", "witty", "sunny", "merry", "nimble", "chill",
  "cosmic", "electric", "mellow", "plucky", "quirky", "snappy", "spry",
  "sturdy", "zesty", "breezy", "chirpy", "dapper", "exciting", "talkative",
  "curious", "fuzzy", "jolly", "peppy", "rustic", "shiny", "smooth", "vivid",
];

const ANIMALS = [
  "platypus", "otter", "falcon", "badger", "lemur", "panda", "koala", "gecko",
  "narwhal", "walrus", "puffin", "ferret", "beaver", "marmot", "raccoon",
  "meerkat", "wombat", "ocelot", "tapir", "heron", "lynx", "moose", "bison",
  "cobra", "finch", "gopher", "iguana", "jackal", "manatee", "newt", "quokka",
  "raven", "seal", "toucan", "vole", "yak", "zebra", "mantis",
];

function pick<T>(arr: readonly T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * A random URL-safe app slug, e.g. `exciting-talkative-platypus`. Guaranteed to
 * satisfy `^[a-z0-9-]{1,40}$`; drops to `adjective-animal` if the three-word
 * form would exceed 40 chars.
 */
export function generateFriendlySlug(): string {
  const animal = pick(ANIMALS);
  const adj1 = pick(ADJECTIVES);
  let adj2 = pick(ADJECTIVES);
  // Avoid the occasional `swift-swift-otter`.
  if (adj2 === adj1) adj2 = pick(ADJECTIVES);

  const three = `${adj1}-${adj2}-${animal}`;
  if (three.length <= 40) return three;
  const two = `${adj1}-${animal}`;
  return two.length <= 40 ? two : animal;
}

/** Title-cased display form of a hyphenated slug, e.g. `Exciting Talkative Platypus`. */
export function friendlyNameFromSlug(slug: string): string {
  return slug
    .split("-")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
