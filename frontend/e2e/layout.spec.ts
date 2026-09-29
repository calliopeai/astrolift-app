import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

/**
 * Spec 44 §6 and §8: the console works down to 768px without a horizontal
 * page scroll, tables scroll inside their own frame, and no long word widens
 * a dialog, sheet or table. Pixel snapshots differ between macOS and CI
 * fonts, so this asserts the rules in a real browser instead, for:
 *
 *   - every story named for a long-string or 768px fixture (LongStrings,
 *     LongLines, LongName, Width768, At768, Tablet, ...), and
 *   - every story of a dialog, sheet or drawer, since those are the frames
 *     a long string breaks first.
 *
 * Each story renders with 768px of content width (the window is 816px: the
 * shared decorator's 24px gutters sit outside it, see playwright.config.ts)
 * and must show (a) no horizontal page scroll, (b) no text past the edge of
 * its nearest dialog, sheet or table frame unless something between them
 * scrolls or truncates it, (c) no dialog or sheet wider than 768px or off
 * screen, (d) nothing past the edge of the story's own 768px frame, and
 * (e) no uncaught page error.
 */

type StoryEntry = { id: string; title: string; name: string; type: string };

const STORYBOOK_DIR = process.env.STORYBOOK_DIR ?? "storybook-static";

const FIXTURE_NAME = /long|width ?768|at ?768|tablet/i;
const FRAME_TITLE = /dialog|sheet|drawer/i;

/**
 * Stories left out, each with the reason. Keep this short: a story belongs
 * here only when the fix needs a design call, never to make a run pass.
 */
const EXCLUDED: Record<string, string> = {};

function selectStories(): StoryEntry[] {
  const index = JSON.parse(readFileSync(path.join(STORYBOOK_DIR, "index.json"), "utf8")) as {
    entries: Record<string, StoryEntry>;
  };
  return Object.values(index.entries)
    .filter((e) => e.type === "story")
    .filter((e) => FIXTURE_NAME.test(e.name) || FRAME_TITLE.test(e.title))
    .filter((e) => !(e.id in EXCLUDED))
    .sort((a, b) => a.id.localeCompare(b.id));
}

/** Waits for the story to mount, fonts to load and the DOM to go quiet. */
async function settle(page: Page) {
  await page.waitForFunction(
    () =>
      document.body.classList.contains("sb-show-errordisplay") ||
      (document.querySelector("#storybook-root")?.childElementCount ?? 0) > 0
  );
  await page.evaluate(async () => {
    await document.fonts.ready;
    await new Promise<void>((resolve) => {
      let timer = setTimeout(done, 250);
      const cap = setTimeout(done, 5000);
      const observer = new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(done, 250);
      });
      function done() {
        observer.disconnect();
        clearTimeout(timer);
        clearTimeout(cap);
        resolve();
      }
      observer.observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
      });
    });
    const finite = document
      .getAnimations()
      .filter((a) => Number.isFinite(a.effect?.getComputedTiming().endTime ?? Infinity));
    await Promise.race([
      Promise.all(finite.map((a) => a.finished.catch(() => undefined))),
      new Promise((r) => setTimeout(r, 2000)),
    ]);
  });
}

type Finding = { rule: string; selector: string; text: string };

/** Runs in the page: collects every layout rule the story breaks. */
function collectFindings(): Finding[] {
  const DIALOG =
    '[role="dialog"],[role="alertdialog"],[data-slot="dialog-content"],[data-slot="sheet-content"],[data-slot="alert-dialog-content"]';
  // A table's frame is its scroll container; a table without one is judged
  // against the dialog or sheet around it.
  const TABLE = '[data-slot="table-container"]';
  const FRAME = `${DIALOG},${TABLE}`;
  const findings: Finding[] = [];

  const selectorOf = (el: Element): string => {
    const parts: string[] = [];
    let node: Element | null = el;
    for (let depth = 0; node && node !== document.body && depth < 4; depth++) {
      let part = node.tagName.toLowerCase();
      const slot = node.getAttribute("data-slot");
      if (node.id) part += `#${node.id}`;
      else if (slot) part += `[data-slot="${slot}"]`;
      else {
        const cls = Array.from(node.classList).slice(0, 3);
        if (cls.length) part += `.${cls.map((c) => CSS.escape(c)).join(".")}`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };
  const excerpt = (s: string) => {
    const t = s.replace(/\s+/g, " ").trim();
    return t.length > 80 ? `${t.slice(0, 77)}...` : t;
  };

  // (a) No horizontal page scroll.
  const root = document.documentElement;
  if (root.scrollWidth > root.clientWidth + 1) {
    // Name the outermost and the innermost element past the edge that no
    // clipping or scrolling ancestor contains: the first is what widens the
    // page, the second is usually the long string that forces it.
    const free = (el: Element) => {
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        if (getComputedStyle(a).overflowX !== "visible") return false;
      }
      return true;
    };
    const past = Array.from(document.body.querySelectorAll("*")).filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.right > root.clientWidth + 1 && free(el);
    });
    const culprits = [...new Set([past[0], past[past.length - 1]].filter(Boolean))];
    if (culprits.length === 0) {
      findings.push({
        rule: `page scrolls horizontally (${root.scrollWidth}px > ${root.clientWidth}px)`,
        selector: "html",
        text: "",
      });
    }
    for (const el of culprits) {
      findings.push({
        rule: `page scrolls horizontally (${root.scrollWidth}px > ${root.clientWidth}px)`,
        selector: selectorOf(el),
        text: excerpt(el.textContent ?? ""),
      });
    }
  }

  // (c) No dialog or sheet wider than 768px or off screen.
  for (const frame of Array.from(document.querySelectorAll(DIALOG))) {
    const r = frame.getBoundingClientRect();
    if (r.width > 769 || (r.width > 0 && (r.left < -1 || r.right > window.innerWidth + 1))) {
      findings.push({
        rule: `frame is ${Math.round(r.width)}px wide at ${Math.round(r.left)}..${Math.round(r.right)} (768px max, on screen)`,
        selector: selectorOf(frame),
        text: excerpt(frame.textContent ?? ""),
      });
    }
  }

  // (d) Nothing past the edge of the story's own 768px frame. Width768 and
  // At768 stories draw that frame with overflow-hidden, which hides from (a)
  // exactly the overflow they exist to show. A negative-margin bleed (the
  // tab rows' -mx-6 into the page gutter) and fixed overlays are exempt.
  const frames768 = Array.from(
    document.querySelectorAll<HTMLElement>("#storybook-root [style]")
  ).filter((el) => el.style.width === "768px");
  for (const frame of frames768) {
    const fr = frame.getBoundingClientRect();
    for (const el of Array.from(frame.querySelectorAll("*"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.right <= fr.right + 1) continue;
      if (getComputedStyle(el).position === "fixed") continue;
      let exempt = false;
      for (let a: Element | null = el; a && a !== frame; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (a !== el && cs.overflowX !== "visible") exempt = true;
        if (parseFloat(cs.marginRight) < 0 && r.right <= a.getBoundingClientRect().right + 1) {
          exempt = true;
        }
        if (exempt) break;
      }
      if (exempt) continue;
      findings.push({
        rule: `element is ${Math.round(r.right - fr.right)}px past the story's 768px frame`,
        selector: selectorOf(el),
        text: excerpt(el.textContent ?? ""),
      });
      break;
    }
  }

  // (b) No text past the edge of its nearest frame, unless an element
  // between them clips or scrolls it (truncate, a scroll area), or the
  // frame itself scrolls horizontally.
  const scrolls = (v: string) => v === "auto" || v === "scroll";
  const reported = new Set<Element>();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue ?? "";
    const el = node.parentElement;
    if (!el || !text.trim() || reported.has(el)) continue;
    const frame = el.closest(FRAME);
    if (!frame) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const t = range.getBoundingClientRect();
    if (t.width === 0 || t.height === 0) continue;
    const f = frame.getBoundingClientRect();
    if (t.right <= f.right + 1 && t.left >= f.left - 1) continue;
    const nowrap = /^(pre|nowrap)$/.test(getComputedStyle(el).whiteSpace);
    let contained = false;
    for (let a: Element | null = el; a; a = a.parentElement) {
      const x = getComputedStyle(a).overflowX;
      // Truncation or a line clamp inside the frame: the text is cut on
      // purpose. The frame itself clipping text is the bug, so not there.
      if ((x === "hidden" || x === "clip") && a !== frame) contained = true;
      // A scroller counts when it is meant to scroll sideways: a table
      // frame, a code block, or a row of unwrapped text. A vertical
      // scroller that a long word forces sideways is the bug.
      if (scrolls(x) && (a.matches('[data-slot="table-container"],pre') || nowrap)) {
        contained = true;
      }
      if (contained || a === frame) break;
    }
    if (contained) continue;
    reported.add(el);
    findings.push({
      rule: `text overflows its frame by ${Math.round(Math.max(t.right - f.right, f.left - t.left))}px (frame ${selectorOf(frame)})`,
      selector: selectorOf(el),
      text: excerpt(text),
    });
  }
  return findings;
}

for (const story of selectStories()) {
  test(`${story.id} lays out at 768px`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(`/iframe.html?id=${encodeURIComponent(story.id)}&viewMode=story`);
    // Storybook's "padded" and "centered" layouts add their own body padding
    // on top of the decorator's gutter; the app has neither.
    await page.addStyleTag({ content: "body { padding: 0 !important; }" });
    await settle(page);

    const renderError = await page.evaluate(() =>
      document.body.classList.contains("sb-show-errordisplay")
        ? (document.querySelector("#error-message")?.textContent ?? "render error")
        : null
    );
    const findings = await page.evaluate(collectFindings);

    const report = [
      ...errors.map((e) => `uncaught page error: ${e}`),
      ...(renderError ? [`story failed to render: ${renderError.trim()}`] : []),
      ...findings.map((f) => `${f.rule}\n    at ${f.selector}\n    text "${f.text}"`),
    ];
    expect(report, `${story.id} (${story.title} / ${story.name})\n${report.join("\n")}`).toEqual(
      []
    );
  });
}
