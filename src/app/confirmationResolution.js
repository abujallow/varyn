/**
 * A synchronous-at-call-time single-flight guard: the first call to the
 * returned function runs `task`; any call that arrives while that task is
 * still in flight is skipped rather than started. Not React state -- this
 * check happens on the JS call stack itself, so it cannot be bypassed by
 * two clicks landing in the same render batch the way state-based guards
 * can. Resets once the task settles (success or failure), so a later,
 * independent task is never permanently blocked.
 */
export function createSingleFlightGuard() {
  let inFlight = false;
  return async function runOnce(task) {
    if (inFlight) return { skipped: true };
    inFlight = true;
    try {
      const result = await task();
      return { skipped: false, result };
    } finally {
      inFlight = false;
    }
  };
}
