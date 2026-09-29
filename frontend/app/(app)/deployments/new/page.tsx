import { StartDeploymentClient } from "./start-deployment-client";

export const metadata = { title: "Start deployment · Astrolift" };

/** The start flow: a page in steps, since it asks for more than three fields (spec 44 §5.4). */
export default function StartDeploymentRoute() {
  return <StartDeploymentClient />;
}
