// The Windows settings pages Pyxi's own windows may ask to open. A window names a page from
// this list ("microphone"); it never passes an address, so nothing else can be opened this
// way. Opening a page shows it and nothing more: no setting is changed.
//
// The app's link opener (openExternalSafe in index.ts) stays http and https only.

/** Every page there is. Add one here only when a notice needs it. */
export const WINDOWS_PAGES = {
  /** Privacy & security > Microphone: where a blocked microphone is let through. */
  microphone: "ms-settings:privacy-microphone",
} as const;

export type WindowsPage = keyof typeof WINDOWS_PAGES;

/** The address for a page on the list, or null for anything else (an address, another page, not a string). */
export function windowsPageUrl(page: unknown, platform: string = process.platform): string | null {
  if (platform !== "win32" || typeof page !== "string") return null;
  return Object.prototype.hasOwnProperty.call(WINDOWS_PAGES, page) ? WINDOWS_PAGES[page as WindowsPage] : null;
}
