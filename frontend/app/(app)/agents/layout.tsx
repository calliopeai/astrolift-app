import { AgentsSubnav } from "./agents-subnav";

// Wraps the Agents section so Skills/Tools are reachable as tabs of Agents
// (#915). The subnav renders only on the list surfaces; it self-hides on an
// agent's detail route, which has its own shell.
export default function AgentsLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <AgentsSubnav />
      {children}
    </>
  );
}
