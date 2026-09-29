import { PeopleClient } from "./people-client";

export const metadata = { title: "People · Access · Astrolift" };

/**
 * Admin › Access › People (access UX design 3.1). No preload: the query is
 * the view's (members, IdP groups or invitations) with the URL's search,
 * chips and page, and the roles column follows the page's people, so a
 * server preload would name one of several requests at best.
 */
export default function PeoplePage() {
  return <PeopleClient />;
}
