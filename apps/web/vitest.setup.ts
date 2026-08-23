import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

/**
 * Give the test environment a working Web Storage.
 *
 * Node's experimental Web Storage is active in this runner and shadows jsdom's,
 * but it resolves `window.localStorage` to an object with no methods — so a
 * storage-backed hook throws on its first `getItem`, before any of its own logic
 * runs. Note the guard tests the *instance*: `Storage.prototype.setItem` exists
 * here even though the instance cannot use it, so checking the prototype would
 * skip the repair that is actually needed.
 */
function installWebStorage(): void {
  for (const name of ["localStorage", "sessionStorage"] as const) {
    if (typeof window[name]?.setItem === "function") {
      continue; // A working implementation is present; leave it alone.
    }

    const entries = new Map<string, string>();
    const storage: Storage = {
      getItem: (key) => entries.get(String(key)) ?? null,
      setItem: (key, value) => void entries.set(String(key), String(value)),
      removeItem: (key) => void entries.delete(String(key)),
      clear: () => entries.clear(),
      key: (index) => [...entries.keys()][index] ?? null,
      get length() {
        return entries.size;
      },
    };

    Object.defineProperty(window, name, {
      configurable: true,
      writable: true,
      value: storage,
    });
  }
}

installWebStorage();

afterEach(() => {
  cleanup();
});
