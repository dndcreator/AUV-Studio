import { createContext, useContext, useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

const ModalDepth = createContext(0);
const topModal = () => Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]'))
  .sort((a, b) => Number(a.getAttribute("data-modal-depth")) - Number(b.getAttribute("data-modal-depth"))).pop();

type ModalProps = {
  title: string;
  kicker?: string;
  subtitle?: ReactNode;
  className?: string;
  backdropClassName?: string;
  closeLabel?: string;
  onClose?: () => void;
  children: ReactNode;
};

export function ModalFrame({ title, kicker, subtitle, className = "", backdropClassName = "", closeLabel, onClose, children }: ModalProps) {
  const titleId = useId();
  const depth = useContext(ModalDepth) + 1;
  const dialog = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const card = dialog.current;
    // Preserve a child's autofocus (for example project search).
    if (topModal() === card && !card?.contains(document.activeElement)) card?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || topModal() !== card) return;
      if (event.key === "Escape" && close.current) {
        event.preventDefault();
        event.stopPropagation();
        close.current();
      }
      if (event.key !== "Tab" || !card) return;
      const controls = Array.from(card.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]'
      )).filter((element) => !element.closest('[hidden], [aria-hidden="true"], details:not([open]) > :not(summary)'));
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!first) { event.preventDefault(); card.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === card)) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === card)) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  return createPortal(
    <ModalDepth.Provider value={depth}>
    <div className={`modal-backdrop ${backdropClassName}`} style={{ zIndex: 100 + depth }} onClick={(event) => {
      if (event.target === event.currentTarget) onClose?.();
    }}>
      <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} data-modal-depth={depth} tabIndex={-1} className={`modal-card ${className}`}>
        <header className="modal-head">
          <div className="modal-heading">
            {kicker ? <span className="result-kicker">{kicker}</span> : null}
            <h2 id={titleId}>{title}</h2>
            {subtitle ? <div className="modal-subtitle">{subtitle}</div> : null}
          </div>
          {onClose ? <button className="btn modal-close" onClick={onClose}>{closeLabel}</button> : null}
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
    </ModalDepth.Provider>, document.body
  );
}

type DrawerProps = {
  open: boolean;
  className: string;
  title: string;
  badge?: ReactNode;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
};

export function DrawerFrame({ open, className, title, badge, closeLabel, onClose, children }: DrawerProps) {
  const titleId = useId();
  return (
    <aside aria-hidden={!open} aria-labelledby={titleId} className={`sidebar studio-drawer ${className} ${open ? "open" : ""}`}>
      <header className="drawer-head">
        <h3 id={titleId}>{title}</h3>
        {badge ? <span className="selection-chip">{badge}</span> : null}
        <button className="drawer-close" onClick={onClose} aria-label={closeLabel}>×</button>
      </header>
      <div className="drawer-body">{children}</div>
    </aside>
  );
}
