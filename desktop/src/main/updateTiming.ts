// When to install a downloaded update by itself: kept apart from updates.ts (Electron) so it's
// easy to test.
export const IDLE_SECONDS = 5 * 60; // no mouse or keyboard for this long: a good moment to update

/** Install a waiting update now? Only when they've stepped away and Pyxi has nothing on. */
export async function goodMomentToUpdate(idleSeconds: number, busy: () => Promise<boolean>): Promise<boolean> {
  if (idleSeconds < IDLE_SECONDS) return false;
  try {
    return !(await busy());
  } catch {
    return false; // can't tell: wait for the next look
  }
}
