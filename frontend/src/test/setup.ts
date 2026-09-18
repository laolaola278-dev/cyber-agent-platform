import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// antd reads these APIs that jsdom does not implement. Without them every
// component test fails inside the library rather than in the code under test.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

class ResizeObserverStub {
  observe() { /* no-op */ }
  unobserve() { /* no-op */ }
  disconnect() { /* no-op */ }
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

// jsdom has no pseudo-element support: `getComputedStyle(el, "::before")`
// reports "Not implemented" and throws. dom-accessibility-api does exactly that
// lookup on every node while computing accessible names, so without this shim
// every `getByRole` query floods the console and gets erratic results. The
// second argument is dropped rather than faked, so callers still get the
// element's own computed style.
const computeStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = ((element: Element) => computeStyle(element)) as typeof window.getComputedStyle;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
