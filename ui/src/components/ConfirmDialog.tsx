import { useEffect, useRef, useState } from "react";

export interface ConfirmRequest {
  readonly kind: "app-stop" | "stop-all" | "unlock";
  readonly appId: string | null;
  readonly title: string;
  readonly body: string;
  readonly affected: readonly string[];
  /** The exact phrase the operator must type, or null when none is required. */
  readonly phrase: string | null;
  readonly reasonRequired: boolean;
  readonly confirmLabel: string;
  readonly destructive: boolean;
}

export interface ConfirmDialogProps {
  readonly request: ConfirmRequest;
  readonly pending: boolean;
  readonly error: string | null;
  readonly onCancel: () => void;
  readonly onConfirm: (reason: string) => void;
}

/**
 * A modal confirmation. Stopping stays easy, but the machine-wide stop and the
 * money unlock require the operator to type the exact phrase.
 */
export function ConfirmDialog({
  request,
  pending,
  error,
  onCancel,
  onConfirm,
}: ConfirmDialogProps) {
  const [phrase, setPhrase] = useState("");
  const [reason, setReason] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);

  const phraseOk = request.phrase === null || phrase === request.phrase;
  const reasonOk = !request.reasonRequired || reason.trim().length > 0;
  const canConfirm = phraseOk && reasonOk && !pending;

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = dialogRef.current?.querySelector<HTMLElement>(
      "input, textarea, button",
    );
    focusable?.focus();
    return () => {
      previous?.focus?.();
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onCancel();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [onCancel]);

  return (
    <div className="backdrop">
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        ref={dialogRef}
      >
        <h2 className="dialog__title" id="confirm-title">
          {request.title}
        </h2>
        <p className="dialog__body">{request.body}</p>

        {request.affected.length > 0 ? (
          <>
            <h3 className="dialog__label">Affected</h3>
            <ul className="dialog__list">
              {request.affected.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </>
        ) : null}

        {request.reasonRequired ? (
          <label className="dialog__field">
            <span className="dialog__label">Reason</span>
            <textarea
              className="dialog__input"
              value={reason}
              rows={2}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
        ) : null}

        {request.phrase !== null ? (
          <label className="dialog__field">
            <span className="dialog__label">
              Type {request.phrase} to confirm
            </span>
            <input
              className="dialog__input"
              type="text"
              value={phrase}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setPhrase(event.target.value)}
            />
          </label>
        ) : null}

        {error !== null ? (
          <p className="dialog__error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="dialog__actions">
          <button type="button" className="btn btn--quiet" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className={request.destructive ? "btn btn--danger" : "btn btn--accent"}
            onClick={() => onConfirm(reason.trim())}
            disabled={!canConfirm}
          >
            {request.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
