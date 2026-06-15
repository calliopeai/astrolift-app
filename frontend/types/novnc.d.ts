// Minimal ambient types for @novnc/novnc (the package ships no .d.ts).
// Only the surface used by components/observability/VncViewer.tsx is
// declared — extend here if more of the RFB API is consumed.
// noVNC 1.7's package.json `exports` maps the package root to core/rfb.js,
// so RFB is imported from "@novnc/novnc" (the deep subpath is not exported).
declare module "@novnc/novnc" {
  export interface RFBOptions {
    credentials?: { username?: string; password?: string; target?: string };
    shared?: boolean;
    repeaterID?: string;
    wsProtocols?: string[];
  }

  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement,
      urlOrChannel: string | WebSocket,
      options?: RFBOptions
    );

    /** Scale the remote framebuffer to fit the container element. */
    scaleViewport: boolean;
    /** Resize the remote session to match the local container. */
    resizeSession: boolean;
    /** Treat the session as view-only (no input forwarded). */
    viewOnly: boolean;
    background: string;

    disconnect(): void;
    focus(): void;
    blur(): void;
  }
}
