import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { createPortal } from "react-dom";

/* Toasts, because failures used to render as a red block next to whatever
   control produced them -- invisible if you had scrolled, and gone the moment
   the panel it lived in unmounted. */

const ToastCtx = createContext(() => {});

export function useToast() {
  return useContext(ToastCtx);
}

let seq = 0;

function Toast({ toast, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(() => onDismiss(toast.id), toast.ttl);
    return () => clearTimeout(t);
  }, [toast.id, toast.ttl, onDismiss]);

  return (
    <div className={`toast ${toast.tone}`} role="status" data-testid="toast">
      <span className="toast-dot" aria-hidden="true" />
      <span className="toast-body">
        <strong>{toast.title}</strong>
        {toast.detail && <span>{toast.detail}</span>}
      </span>
      <button className="toast-x" onClick={() => onDismiss(toast.id)}
              aria-label="Dismiss" data-testid="toast-dismiss">×</button>
    </div>
  );
}

export default function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((title, opts = {}) => {
    const id = ++seq;
    setToasts((list) => [
      ...list.slice(-3),
      { id, title, detail: opts.detail, tone: opts.tone || "info",
        ttl: opts.ttl ?? (opts.tone === "error" ? 7000 : 4200) },
    ]);
    return id;
  }, []);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      {createPortal(
        <div className="toast-stack" data-testid="toast-stack">
          {toasts.map((t) => <Toast key={t.id} toast={t} onDismiss={dismiss} />)}
        </div>,
        document.body,
      )}
    </ToastCtx.Provider>
  );
}
