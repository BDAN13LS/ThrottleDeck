import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog, type ConfirmRequest } from "./ConfirmDialog";

const APP_STOP: ConfirmRequest = {
  kind: "app-stop",
  appId: "execution",
  title: "Stop brokered REST access",
  body: "Brokered REST reads stop for these caller labels. Direct venue requests and WebSocket streams are not affected, and the application keeps running.",
  affected: ["execution", "execution-lag"],
  phrase: null,
  reasonRequired: false,
  confirmLabel: "Stop access",
  destructive: true,
};

const STOP_ALL: ConfirmRequest = {
  kind: "stop-all",
  appId: null,
  title: "Stop all brokered REST access",
  body: "Every brokered REST read on this machine stops, for known and unknown callers. Direct venue requests and WebSocket streams are not affected.",
  affected: ["Every registered application", "Unknown callers"],
  phrase: "STOP ALL",
  reasonRequired: false,
  confirmLabel: "Stop all brokered REST access",
  destructive: true,
};

const UNLOCK: ConfirmRequest = {
  kind: "unlock",
  appId: "execution",
  title: "Unlock Execution Bot new money orders",
  body: "This allows Execution Bot to submit new real-money orders again. Existing orders were not cancelled, and nothing is cancelled or flattened here.",
  affected: [],
  phrase: "UNLOCK NEW ORDERS",
  reasonRequired: true,
  confirmLabel: "Unlock",
  destructive: false,
};

function renderDialog(
  request: ConfirmRequest,
  overrides: Partial<Parameters<typeof ConfirmDialog>[0]> = {},
) {
  const handlers = { onCancel: vi.fn(), onConfirm: vi.fn() };
  render(
    <ConfirmDialog
      request={request}
      pending={false}
      error={null}
      {...handlers}
      {...overrides}
    />,
  );
  return handlers;
}

describe("ConfirmDialog", () => {
  it("names the affected caller labels for a per-application stop", () => {
    renderDialog(APP_STOP);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleName("Stop brokered REST access");
    expect(screen.getByText("execution-lag")).toBeVisible();
    expect(screen.getByText(/WebSocket streams are not affected/)).toBeVisible();
  });

  it("confirms a per-application stop without a typed phrase", async () => {
    const handlers = renderDialog(APP_STOP);
    await userEvent.click(screen.getByRole("button", { name: "Stop access" }));
    expect(handlers.onConfirm).toHaveBeenCalledWith("");
  });

  it("requires the exact STOP ALL phrase for global broker access", async () => {
    const handlers = renderDialog(STOP_ALL);
    const confirm = screen.getByRole("button", {
      name: "Stop all brokered REST access",
    });
    expect(confirm).toBeDisabled();

    const phrase = screen.getByLabelText(/Type STOP ALL to confirm/);
    await userEvent.type(phrase, "stop all");
    expect(confirm).toBeDisabled();
    await userEvent.clear(phrase);
    await userEvent.type(phrase, "STOP ALL");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(handlers.onConfirm).toHaveBeenCalledWith("");
  });

  it("requires the typed phrase and a reason to unlock trading", async () => {
    const handlers = renderDialog(UNLOCK);
    const confirm = screen.getByRole("button", { name: "Unlock" });
    expect(confirm).toBeDisabled();

    await userEvent.type(
      screen.getByLabelText(/Type UNLOCK NEW ORDERS to confirm/),
      "UNLOCK NEW ORDERS",
    );
    expect(confirm).toBeDisabled();

    await userEvent.type(
      screen.getByLabelText(/Reason/),
      "Reviewed the direct-caller warning.",
    );
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(handlers.onConfirm).toHaveBeenCalledWith(
      "Reviewed the direct-caller warning.",
    );
    expect(screen.getByText(/nothing is cancelled or flattened here/)).toBeVisible();
  });

  it("closes on cancel and on Escape", async () => {
    const handlers = renderDialog(APP_STOP);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(handlers.onCancel).toHaveBeenCalledOnce();

    handlers.onCancel.mockClear();
    await userEvent.keyboard("{Escape}");
    expect(handlers.onCancel).toHaveBeenCalledOnce();
  });

  it("moves focus into the dialog and keeps the decision disabled while pending", () => {
    renderDialog(UNLOCK, { pending: true });
    const dialog = screen.getByRole("dialog");
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    expect(screen.getByRole("button", { name: "Unlock" })).toBeDisabled();
  });

  it("shows the not-applied message when the service refuses", () => {
    renderDialog(APP_STOP, {
      error: "Not applied — Governor did not confirm the change.",
    });
    expect(
      screen.getByText("Not applied — Governor did not confirm the change."),
    ).toBeVisible();
  });
});
