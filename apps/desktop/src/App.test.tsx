import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("workbench", () => {
  it("renders the compact inspector navigation", () => {
    render(<App />);

    expect(screen.getAllByText("DualCode Workbench").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "状态" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "规则" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "交接" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "仓库" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "变更" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行日志" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "恢复" })).toBeNull();
  });
});
