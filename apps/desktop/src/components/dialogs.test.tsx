import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InputDialog } from "./dialogs";

describe("application dialogs", () => {
  it("focuses the first field, traps Tab, and closes with Escape", async () => {
    const close = vi.fn();
    render(
      <InputDialog
        title="选择路径"
        placeholder="绝对路径"
        onSubmit={vi.fn()}
        onClose={close}
      />,
    );
    const input = screen.getByPlaceholderText("绝对路径");
    await waitFor(() => expect(document.activeElement).toBe(input));

    fireEvent.keyDown(input, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();

    const open = screen.getByRole("button", { name: "打开" });
    open.focus();
    fireEvent.keyDown(open, { key: "Tab" });
    expect(document.activeElement).toBe(input);
  });
});
