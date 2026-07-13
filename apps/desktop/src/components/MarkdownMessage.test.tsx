import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownMessage } from "./MarkdownMessage";

describe("MarkdownMessage", () => {
  it("renders GFM lists, links, code blocks, and tables", () => {
    render(
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
    expect(screen.getByText("const answer = 42;")).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
  });

  it("does not render raw HTML", () => {
    const { container } = render(
      <MarkdownMessage text={'<img src="x" onerror="alert(1)">'} />,
    );
    expect(container.querySelector("img")).toBeNull();
  });
});
