import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@monaco-editor/react", () => ({
  default: ({ value }: { value: string }) => (
    <pre data-testid="editor">{value}</pre>
  ),
  loader: { config: vi.fn() },
}));
vi.mock("monaco-editor/esm/vs/editor/editor.api", () => ({}));

import { DiffPanel, splitGitDiff } from "./DiffPanel";

const diff = [
  "diff --git a/src/first.ts b/src/first.ts",
  "--- a/src/first.ts",
  "+++ b/src/first.ts",
  "@@ -1 +1 @@",
  "-old",
  "+new",
  "diff --git a/src/second.ts b/src/second.ts",
  "--- a/src/second.ts",
  "+++ b/src/second.ts",
  "@@ -0,0 +1 @@",
  "+second",
].join("\n");

describe("DiffPanel", () => {
  it("splits a unified diff into files", () => {
    expect(splitGitDiff(diff).map((file) => file.path)).toEqual([
      "src/first.ts",
      "src/second.ts",
    ]);
  });

  it("navigates between changed files", async () => {
    render(<DiffPanel diff={diff} />);

    expect((await screen.findByTestId("editor")).textContent).toContain("+new");
    fireEvent.click(screen.getByRole("button", { name: /src\/second.ts/ }));
    expect(screen.getByTestId("editor").textContent).toContain("+second");
    expect(screen.getByTestId("editor").textContent).not.toContain("+new");
  });
});
