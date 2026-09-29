import { PeopleClient } from "./people-client";

export const metadata = { title: "People · Access · Astrolift" };

/**
 * Admin › Access › People (access UX design 3.1). No preload: the list walks
 * the members, bindings or invitations its view needs, with the search from
 * the URL, so a variable-less SSR preload would never be the request made.
 */
export default function PeoplePage() {
  return <PeopleClient />;
}
