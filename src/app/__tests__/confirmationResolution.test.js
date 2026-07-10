import { describe, expect, it, vi } from "vitest";
import { createSingleFlightGuard } from "../confirmationResolution.js";

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("createSingleFlightGuard", () => {
  it("runs the task on the first call", async () => {
    const guard = createSingleFlightGuard();
    const task = vi.fn().mockResolvedValue("done");
    const outcome = await guard(task);
    expect(task).toHaveBeenCalledTimes(1);
    expect(outcome).toEqual({ skipped: false, result: "done" });
  });

  it("skips a second call that arrives while the first is still in flight", async () => {
    const guard = createSingleFlightGuard();
    const gate = deferred();
    const task = vi.fn().mockReturnValue(gate.promise);

    const firstCall = guard(task);
    // Fired before the first call's task has resolved -- simulates a rapid
    // second click (or two clicks landing in the same interaction cycle).
    const secondOutcome = await guard(task);

    expect(secondOutcome).toEqual({ skipped: true });
    expect(task).toHaveBeenCalledTimes(1);

    gate.resolve("first-result");
    const firstOutcome = await firstCall;
    expect(firstOutcome).toEqual({ skipped: false, result: "first-result" });
  });

  it("resets after completion so a later, independent call runs normally", async () => {
    const guard = createSingleFlightGuard();
    const taskA = vi.fn().mockResolvedValue("a");
    const taskB = vi.fn().mockResolvedValue("b");

    await guard(taskA);
    const outcomeB = await guard(taskB);

    expect(taskB).toHaveBeenCalledTimes(1);
    expect(outcomeB).toEqual({ skipped: false, result: "b" });
  });

  it("does not swallow a rejection from the task -- callers still see the error", async () => {
    const guard = createSingleFlightGuard();
    const failure = new Error("network error");
    const task = vi.fn().mockRejectedValue(failure);

    await expect(guard(task)).rejects.toBe(failure);
  });

  it("releases the lock even when the task throws, so a retry can proceed", async () => {
    const guard = createSingleFlightGuard();
    const task = vi.fn().mockRejectedValue(new Error("boom"));

    await expect(guard(task)).rejects.toThrow("boom");

    const retryTask = vi.fn().mockResolvedValue("recovered");
    const outcome = await guard(retryTask);
    expect(outcome).toEqual({ skipped: false, result: "recovered" });
  });

  it("treats approve and deny identically -- the guard has no decision-specific behavior", async () => {
    const guard = createSingleFlightGuard();
    const approveTask = vi.fn().mockResolvedValue("approved");
    const outcome = await guard(approveTask);
    expect(outcome.result).toBe("approved");

    const denyTask = vi.fn().mockResolvedValue("denied");
    const denyOutcome = await guard(denyTask);
    expect(denyOutcome.result).toBe("denied");
  });
});
