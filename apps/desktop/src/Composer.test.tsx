import { createRef } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Composer, ImageAttachment } from "./App";

afterEach(cleanup);

function renderComposer(
  overrides: Partial<React.ComponentProps<typeof Composer>> = {},
) {
  const props: React.ComponentProps<typeof Composer> = {
    text: "",
    setText: vi.fn(),
    mode: "codex",
    setMode: vi.fn(),
    run: vi.fn(),
    cancel: vi.fn(async () => undefined),
    running: false,
    offline: false,
    fileInput: createRef<HTMLInputElement>(),
    upload: vi.fn(async () => undefined),
    attachments: [],
    removeAttachment: vi.fn(),
    notify: vi.fn(),
    ...overrides,
  };
  render(<Composer {...props} />);
  return props;
}

describe("Composer", () => {
  it("previews image attachments as thumbnails in the tray", () => {
    renderComposer({
      attachments: [
        { id: "img-1", name: "screen.png", media_type: "image/png" },
        { id: "txt-1", name: "note.txt", media_type: "text/plain" },
      ],
    });

    const thumb = document.querySelector(
      ".attachment-thumb",
    ) as HTMLImageElement;
    expect(thumb).toBeTruthy();
    expect(thumb.alt).toBe("screen.png");
    expect(
      document.querySelectorAll(".attachment-chip .attachment-thumb"),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "移除附件 note.txt" }),
    ).toBeTruthy();
  });

  it("uses one attachment action and reports files beyond the limit", () => {
    const upload = vi.fn(async () => undefined);
    const notify = vi.fn();
    const attachments = Array.from({ length: 7 }, (_, index) => ({
      id: String(index),
      name: `${index}.txt`,
      media_type: "text/plain",
    }));
    renderComposer({ upload, notify, attachments });

    expect(screen.getAllByRole("button", { name: "添加附件" })).toHaveLength(1);
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["a"], "a.txt"), new File(["b"], "b.txt")] },
    });

    expect(upload).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith("error", "每条消息最多添加 8 个附件");
  });

  it("keeps Shift+Enter as a newline shortcut and highlights drag hover", () => {
    const run = vi.fn();
    renderComposer({ run });
    const textarea =
      screen.getByPlaceholderText("输入消息；可以拖入文件或粘贴截图…");

    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(run).not.toHaveBeenCalled();
    const composer = textarea.closest(".composer")!;
    fireEvent.dragEnter(composer);
    expect(composer.classList.contains("dragging")).toBe(true);
  });
});

describe("ImageAttachment", () => {
  it("zooms with a non-passive wheel handler and supports drag panning", () => {
    window.PointerEvent = MouseEvent as typeof PointerEvent;
    HTMLElement.prototype.setPointerCapture = vi.fn();
    HTMLElement.prototype.releasePointerCapture = vi.fn();
    render(<ImageAttachment url="image.png" name="image.png" />);
    fireEvent.click(screen.getByRole("button", { name: "预览图片 image.png" }));

    const canvas = document.querySelector(".image-preview-canvas")!;
    const image = screen.getAllByAltText("image.png")[1];
    fireEvent.wheel(canvas, { deltaY: -100 });
    expect(image.getAttribute("style")).toContain("scale(1.15)");

    fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 10, clientY: 10 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 30, clientY: 40 });
    expect(image.getAttribute("style")).toContain("translate(20px, 30px)");
  });
});
