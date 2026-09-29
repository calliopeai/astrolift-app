import { notFound } from "next/navigation";

import {
  type PlaygroundHistorySession,
  PlaygroundHistoryScreen,
} from "@/components/screens/playground/PlaygroundHistoryScreen";

import { isRouteEnabled } from "@/lib/route-flags";

const sessions: PlaygroundHistorySession[] = [
  {
    date: "2026-02-23",
    prompt: "Explain zero-shot prompting in simple terms",
    model: "Genesis",
    tokens: 312,
    status: "completed",
  },
  {
    date: "2026-02-22",
    prompt: "Write a unit test for a TypeScript function that validates email",
    model: "Explorer",
    tokens: 541,
    status: "completed",
  },
  {
    date: "2026-02-22",
    prompt: "Summarise the key points from this research paper excerpt…",
    model: "Quantum",
    tokens: 1024,
    status: "completed",
  },
  {
    date: "2026-02-21",
    prompt: "Translate the following paragraph to Spanish",
    model: "Genesis",
    tokens: 198,
    status: "completed",
  },
  {
    date: "2026-02-21",
    prompt: "Generate SQL to find top 10 customers by revenue",
    model: "Explorer",
    tokens: 287,
    status: "completed",
  },
  {
    date: "2026-02-20",
    prompt: "Draft a product launch announcement email for our new API",
    model: "Genesis",
    tokens: 623,
    status: "completed",
  },
  {
    date: "2026-02-20",
    prompt: "What are the pros and cons of microservices?",
    model: "Quantum",
    tokens: 445,
    status: "completed",
  },
  {
    date: "2026-02-19",
    prompt: "Create a Python script to parse CSV files and output JSON",
    model: "Explorer",
    tokens: 789,
    status: "completed",
  },
  {
    date: "2026-02-18",
    prompt: "Review this code for security vulnerabilities",
    model: "Quantum",
    tokens: 932,
    status: "error",
  },
  {
    date: "2026-02-17",
    prompt: "List 10 creative names for a SaaS analytics product",
    model: "Genesis",
    tokens: 156,
    status: "completed",
  },
];

export default function PlaygroundHistoryPage() {
  if (!isRouteEnabled("/playground")) notFound();

  return <PlaygroundHistoryScreen sessions={sessions} />;
}
