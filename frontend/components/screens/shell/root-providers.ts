/**
 * The tooltip context the root layout wraps every page in. It renders no UI
 * of its own, so it is re-exported here for app/layout.tsx rather than
 * imported from `@/components/ui/*`, which routes may not reach into.
 */
export { TooltipProvider } from "@/components/ui/tooltip";
