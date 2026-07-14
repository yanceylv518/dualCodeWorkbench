import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

const css = (name: string) =>
  readFileSync(new URL(name, import.meta.url), "utf-8");

const index = css("./index.css");
const styles = [
  css("./contract.css"),
  css("./handoff.css"),
  index,
  css("./message-actions.css"),
  css("./recovery.css"),
].join("\n");

describe("desktop visual policy", () => {
  it("does not use text smaller than 11px", () => {
    expect(styles).not.toMatch(/font-size:\s*(?:[0-9]|10)px/);
    // font 简写同样受最小字号约束（含换行书写的 font: \n 10px …）。
    expect(styles).not.toMatch(/font:\s*(?:[0-9]|10)px/);
    expect(styles).not.toMatch(/font:\n\s*(?:[0-9]|10)px/);
  });

  it("defines shared typography and keyboard focus tokens", () => {
    expect(index).toContain("--text-xs: 11px");
    expect(index).toContain("--text-base: 13px");
    expect(index).toContain(":focus-visible");
    expect(index).toContain("var(--focus-ring)");
  });

  it("reveals message timestamps on hover while touch stays visible", () => {
    expect(index).toContain(".message-card:hover .message-body header time");
    expect(index).toContain(
      ".message-card:focus-within .message-body header time",
    );
  });

  it("defines the U5 surface, radius and motion tokens", () => {
    expect(index).toContain("--text-reading: 16px");
    expect(index).toContain("--reading-leading: 1.7");
    expect(index).toContain("--surface-1:");
    expect(index).toContain("--radius-md: 10px");
    expect(index).toContain("--transition-fast: 150ms ease");
    expect(index).toContain("@media (prefers-reduced-motion: reduce)");
  });

  it("keeps message spacing uniform without hidden actions taking layout space", () => {
    expect(index).toContain("margin: 0 auto 24px");
    expect(index).toContain("--conversation-content-width: 768px");
    expect(index).toContain("--conversation-turn-gap: 28px");
    expect(index).toContain("max-width: 75%");
    expect(index).toContain("background: transparent !important");
    const actions = css("./message-actions.css");
    expect(actions).toContain("position: absolute");
    expect(actions).toContain(".message-card:hover .message-actions");
    expect(actions).toContain(".message-card:focus-within .message-actions");
    expect(actions).toContain("@media (hover: none)");
  });

  it("renders thinking as indented text and a full-width inline disclosure", () => {
    expect(index).toContain(".thinking-pulse");
    expect(index).not.toContain(".thinking-block.running p::after");
    expect(index).toMatch(
      /\.thought-pill > summary \{[\s\S]*?display: flex;[\s\S]*?width: 100%;/,
    );
    expect(index).toMatch(
      /\.thought-pill > p \{[\s\S]*?animation: thought-reveal 150ms ease;/,
    );
  });

  it("renders tools as independent borderless rows without an outer card", () => {
    expect(index).toMatch(
      /\.agent-activity \{[\s\S]*?gap: 8px;[\s\S]*?border: 0 !important;[\s\S]*?background: transparent !important;/,
    );
    expect(index).toMatch(
      /\.tool-activity-row \{[\s\S]*?border: 0;[\s\S]*?border-radius: var\(--radius-md\);/,
    );
    expect(index).toContain(".tool-activity-row.failed .tool-activity-status");
  });
});
