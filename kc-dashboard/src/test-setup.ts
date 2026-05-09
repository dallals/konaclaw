import "@testing-library/jest-dom";

// jsdom 29 + vitest 4 has a broken localStorage in this combo — it exposes
// `window.localStorage` as a plain object with no methods. Define a minimal
// in-memory Storage implementation if the real API isn't usable.
function makeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    clear: () => { store = {}; },
    getItem: (k) => (k in store ? store[k] : null),
    key: (i) => Object.keys(store)[i] ?? null,
    removeItem: (k) => { delete store[k]; },
    setItem: (k, v) => { store[k] = String(v); },
  };
}

if (typeof window !== "undefined") {
  if (typeof window.localStorage?.setItem !== "function") {
    Object.defineProperty(window, "localStorage", {
      value: makeStorage(), configurable: true, writable: true,
    });
  }
  if (typeof window.sessionStorage?.setItem !== "function") {
    Object.defineProperty(window, "sessionStorage", {
      value: makeStorage(), configurable: true, writable: true,
    });
  }
}
