import { useEffect, useRef, useState } from "react";

const focusable =
  'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useDialogFocus<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const previous = document.activeElement as HTMLElement | null;
    const elements = () =>
      Array.from(dialog.querySelectorAll<HTMLElement>(focusable));
    window.setTimeout(() => elements()[0]?.focus());
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key !== "Tab") return;
      const items = elements();
      if (!items.length) return;
      const first = items[0];
      const last = items.at(-1)!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    dialog.addEventListener("keydown", keydown);
    return () => {
      dialog.removeEventListener("keydown", keydown);
      previous?.focus();
    };
  }, [onClose]);
  return ref;
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = "确认",
  danger = false,
  onConfirm,
  onClose,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const ref = useDialogFocus<HTMLDivElement>(onClose);
  return (
    <div className="dialog-backdrop">
      <div
        ref={ref}
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <h2 id="confirm-title">{title}</h2>
        <p>{message}</p>
        <footer>
          <button autoFocus onClick={onClose}>
            取消
          </button>
          <button className={danger ? "danger" : "primary"} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
}

export function InputDialog({
  title,
  placeholder,
  onSubmit,
  onClose,
}: {
  title: string;
  placeholder: string;
  onSubmit: (value: string) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState("");
  const ref = useDialogFocus<HTMLDivElement>(onClose);
  return (
    <div className="dialog-backdrop">
      <div
        ref={ref}
        className="confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="input-title"
      >
        <h2 id="input-title">{title}</h2>
        <input
          autoFocus
          value={value}
          placeholder={placeholder}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Enter" && value.trim()) onSubmit(value.trim());
          }}
        />
        <footer>
          <button onClick={onClose}>取消</button>
          <button
            className="primary"
            disabled={!value.trim()}
            onClick={() => onSubmit(value.trim())}
          >
            打开
          </button>
        </footer>
      </div>
    </div>
  );
}
