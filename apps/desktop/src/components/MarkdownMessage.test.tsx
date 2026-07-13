import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownMessage } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  const writeText = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
  });

  it("renders GFM lists, links, code blocks, and tables", () => {
    const { container } = render(
      <MarkdownMessage
        text={[
          "- first item",
          "- [documentation](https://example.com)",
          "",
          "```ts",
          "const answer = 42;",
          "```",
          "",
          "| Name | State |",
          "| --- | --- |",
          "| Build | Ready |",
        ].join("\n")}
      />,
    );

    expect(screen.getByRole("list")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(
      screen
        .getByRole("link", { name: "documentation" })
        .getAttribute("target"),
    ).toBe("_blank");
    expect(container.querySelector("code.hljs.language-ts")?.textContent).toBe(
      "const answer = 42;",
    );
    expect(within(container).getByText("ts")).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
  });

  it("falls back to plain text for an unknown language", () => {
    const { container } = render(
      <MarkdownMessage
        text={["```unknown", "<tag>plain</tag>", "```"].join("\n")}
      />,
    );
    expect(container.querySelector("code.hljs")).toBeNull();
    expect(screen.getByText("<tag>plain</tag>")).toBeTruthy();
  });

  it("copies the original code and shows inline feedback", async () => {
    const { container } = render(
      <MarkdownMessage
        text={["```js", "const value = 1;", "```"].join("\n")}
      />,
    );
    fireEvent.click(
      within(container).getByRole("button", { name: "复制代码" }),
    );
    expect(writeText).toHaveBeenCalledWith("const value = 1;");
    expect(
      await within(container).findByRole("button", { name: "代码已复制" }),
    ).toBeTruthy();
  });

  it("collapses code over 400 lines and expands it on demand", () => {
    const code = Array.from({ length: 401 }, (_, index) => `line ${index + 1}`);
    const { container } = render(
      <MarkdownMessage text={["```text", ...code, "```"].join("\n")} />,
    );
    expect(within(container).queryByText("line 401")).toBeNull();
    fireEvent.click(
      within(container).getByRole("button", { name: "展开全部（401 行）" }),
    );
    expect(within(container).getByText(/line 401/)).toBeTruthy();
  });

  it("does not render raw HTML", () => {
    const { container } = render(
      <MarkdownMessage text={'<img src="x" onerror="alert(1)">'} />,
    );
    expect(container.querySelector("img")).toBeNull();
  });
});
