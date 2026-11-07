// Card buttons (overlay and panel) act only on real input.
/** Answers count only from real input (a person's click or key), never a script's
 * dispatchEvent or element.click(). Pyxi's own mouse can't approve them either: its hands refuse
 * Pyxi's windows, and the brain ignores answers while they move (pyxi/hands.py). */
export function trusted(fn: () => void): (e: { isTrusted: boolean }) => void {
  return (e) => {
    if (e.isTrusted) fn();
  };
}
