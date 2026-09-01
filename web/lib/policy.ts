/** Widening is anything → auto_approve. That write is N-T8, so it must be loud. */

export const AUTO_APPROVE = "auto_approve";

export function isWidening(currentAction: string, nextAction: string): boolean {
  return nextAction === AUTO_APPROVE && currentAction !== AUTO_APPROVE;
}

export const WIDEN_WARNING =
  "WIDENS AUTHORITY — this edit sets auto_approve. The policy table is software-writable (N-T8). A token that can load this page can silence the device for this pattern. Confirm you intend to widen.";
