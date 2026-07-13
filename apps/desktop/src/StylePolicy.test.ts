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
  });

  it("defines shared typography and keyboard focus tokens", () => {
    expect(index).toContain("--text-xs: 11px");
    expect(index).toContain("--text-base: 13px");
    expect(index).toContain(":focus-visible");
    expect(index).toContain("var(--focus-ring)");
  });

  it("keeps message spacing uniform without hidden actions taking layout space", () => {
    expect(index).toContain("margin: 0 auto 24px");
    expect(index).toContain("padding: 10px 0 12px 14px");
    const actions = css("./message-actions.css");
    expect(actions).toContain("position: absolute");
    expect(actions).toContain(".message-card:hover .message-actions");
    expect(actions).toContain(".message-card:focus-within .message-actions");
    expect(actions).toContain("@media (hover: none)");
  });
});
