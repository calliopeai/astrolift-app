import { render } from "@testing-library/react";

type PortableStory = {
  run: (context: {
    canvasElement: HTMLElement;
    testingLibraryRender: typeof render;
  }) => Promise<void>;
};

export const runStory = (story: PortableStory, canvasElement: HTMLElement) =>
  // Storybook's default renderer owns a separate React root that RTL cleanup
  // cannot unmount when Vitest tears down jsdom. Register it with RTL instead.
  story.run({ canvasElement, testingLibraryRender: render });
