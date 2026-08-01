import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Undo/redo over the whole edit recipe.
 *
 * The editor already describes a clip as one plain object, so history is just a
 * stack of those objects -- no per-control undo logic, and a control added
 * later is covered automatically.
 *
 * Rapid changes are COALESCED: dragging a trim handle fires dozens of updates a
 * second, and undo should step back over "that drag", not over one pixel of it.
 * A change lands as its own history entry only after the value has been still
 * for `quietMs`, or when the change is explicitly marked as discrete.
 */
const LIMIT = 100;
const QUIET_MS = 450;

export function useEditHistory(initial) {
  const [state, setState] = useState(initial);
  const past = useRef([]);
  const future = useRef([]);
  const pending = useRef(null);
  const timer = useRef(null);
  const [, force] = useState(0);
  const bump = () => force((n) => n + 1);

  const commitPending = useCallback(() => {
    if (pending.current === null) return;
    past.current.push(pending.current);
    if (past.current.length > LIMIT) past.current.shift();
    pending.current = null;
    bump();
  }, []);

  /** Apply a change. `discrete` marks a click-like action worth its own step. */
  const apply = useCallback((patch, { discrete = false } = {}) => {
    setState((prev) => {
      const next = typeof patch === "function" ? patch(prev) : { ...prev, ...patch };
      if (JSON.stringify(next) === JSON.stringify(prev)) return prev;

      future.current = [];
      if (discrete) {
        // Flush any drag-in-progress first so the two do not merge into one
        // confusing step.
        if (pending.current !== null) {
          past.current.push(pending.current);
          pending.current = null;
        }
        past.current.push(prev);
        if (past.current.length > LIMIT) past.current.shift();
      } else {
        if (pending.current === null) pending.current = prev;
        clearTimeout(timer.current);
        timer.current = setTimeout(commitPending, QUIET_MS);
      }
      bump();
      return next;
    });
  }, [commitPending]);

  const undo = useCallback(() => {
    clearTimeout(timer.current);
    commitPending();
    setState((prev) => {
      const previous = past.current.pop();
      if (previous === undefined) return prev;
      future.current.push(prev);
      bump();
      return previous;
    });
  }, [commitPending]);

  const redo = useCallback(() => {
    setState((prev) => {
      const next = future.current.pop();
      if (next === undefined) return prev;
      past.current.push(prev);
      bump();
      return next;
    });
  }, []);

  /** Back to the clip as the pipeline produced it, undoably. */
  const reset = useCallback(() => {
    clearTimeout(timer.current);
    setState((prev) => {
      if (JSON.stringify(prev) === JSON.stringify(initial)) return prev;
      if (pending.current !== null) {
        past.current.push(pending.current);
        pending.current = null;
      }
      past.current.push(prev);
      future.current = [];
      bump();
      return initial;
    });
  }, [initial]);

  useEffect(() => () => clearTimeout(timer.current), []);

  useEffect(() => {
    function onKey(e) {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod || e.key.toLowerCase() !== "z") return;
      // Never hijack undo inside a text field -- people expect it to undo typing.
      const el = document.activeElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable)) {
        return;
      }
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  return {
    state,
    apply,
    undo,
    redo,
    reset,
    canUndo: past.current.length > 0 || pending.current !== null,
    canRedo: future.current.length > 0,
    isDirty: JSON.stringify(state) !== JSON.stringify(initial),
  };
}
