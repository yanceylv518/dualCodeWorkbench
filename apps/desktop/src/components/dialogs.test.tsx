import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InputDialog } from "./dialogs";

afterEach(cleanup);

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

  it("does not submit InputDialog while the input method is composing", () => {
    const submit = vi.fn();
    render(
      <InputDialog
        title="选择路径"
        placeholder="绝对路径"
        onSubmit={submit}
        onClose={vi.fn()}
      />,
    );
    const input = screen.getByPlaceholderText("绝对路径");
    fireEvent.change(input, { target: { value: "中文路径" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });

    expect(submit).not.toHaveBeenCalled();
  });
});
