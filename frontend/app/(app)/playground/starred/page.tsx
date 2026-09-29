import { notFound } from "next/navigation";

import {
  type PlaygroundStarredPrompt,
  PlaygroundStarredScreen,
} from "@/components/screens/playground/PlaygroundStarredScreen";

import { isRouteEnabled } from "@/lib/route-flags";

const starred: PlaygroundStarredPrompt[] = [
  {
    title: "Unit test generator",
    prompt:
      "Write a comprehensive unit test suite for the following TypeScript function. Cover edge cases, null inputs, and boundary conditions.",
    model: "Explorer",
    date: "2026-02-20",
  },
  {
    title: "SQL optimiser",
    prompt:
      "Analyse this SQL query and suggest optimisations for performance. Identify missing indexes and rewrite subqueries where appropriate.",
    model: "Quantum",
    date: "2026-02-18",
  },
  {
    title: "Email drafter",
    prompt:
      "Draft a professional email to announce a new product feature. Tone should be enthusiastic but concise. Include a call to action.",
    model: "Genesis",
    date: "2026-02-15",
  },
  {
    title: "Code review checklist",
    prompt:
      "Review the following code diff and provide structured feedback: correctness, security, readability, and performance.",
    model: "Quantum",
    date: "2026-02-12",
  },
  {
    title: "Meeting summariser",
    prompt:
      "Summarise the following meeting transcript into bullet points grouped by topic. Highlight action items and owners.",
    model: "Genesis",
    date: "2026-02-10",
  },
  {
    title: "API documentation writer",
    prompt:
      "Generate OpenAPI-style documentation for the following endpoint, including descriptions, parameter types, and example responses.",
    model: "Explorer",
    date: "2026-02-08",
  },
];

export default function PlaygroundStarredPage() {
  if (!isRouteEnabled("/playground")) notFound();

  return <PlaygroundStarredScreen items={starred} />;
}
