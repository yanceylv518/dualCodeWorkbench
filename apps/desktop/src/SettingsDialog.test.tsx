import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { SettingsDialog } from "./SettingsDialog";
import type { AgentSettings } from "./types";

const settings: AgentSettings = {
  enable_real_agents: true,
  codex_executable: "codex",
  codex_model: "",
  codex_reasoning_effort: "medium",
  codex_permission_mode: "safe",
  claude_executable: "claude",
  claude_model: "opus",
  claude_reasoning_effort: "medium",
  claude_ssh_enabled: false,
  claude_ssh_host: "",
  claude_ssh_username: "",
  claude_ssh_port: 22,
  claude_ssh_known_hosts: "",
  claude_ssh_client_key: "",
  claude_ssh_remote_root: "",
  claude_ssh_projects_root: "",
  claude_ssh_executable: "/usr/local/bin/claude",
  test_executable: "",
  test_arguments: [],
};

vi.mock("./api", () => ({
  fetchAgentSettings: vi.fn(),
  fetchAgentHealth: vi.fn(async () => ({})),
  fetchAgentModels: vi.fn(async () => ({ codex: [], claude: [] })),
  saveAgentSettings: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderLoaded = async (onClose: () => void) => {
  vi.mocked(api.fetchAgentSettings).mockResolvedValue(settings);
  render(<SettingsDialog onClose={onClose} />);
  await waitFor(() =>
    expect(screen.getByDisplayValue("codex")).toBeTruthy(),
  );
};

describe("settings dialog", () => {
  it("closes directly when nothing has changed", async () => {
    const close = vi.fn();
    await renderLoaded(close);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("asks for confirmation before discarding unsaved changes", async () => {
    const close = vi.fn();
    await renderLoaded(close);

    fireEvent.change(screen.getByDisplayValue("codex"), {
      target: { value: "codex-nightly" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByText("放弃未保存的修改？")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "放弃修改" }));
    expect(close).toHaveBeenCalledOnce();
  });

  it("keeps the dialog open when the user cancels discarding", async () => {
    const close = vi.fn();
    await renderLoaded(close);

    fireEvent.change(screen.getByDisplayValue("codex"), {
      target: { value: "codex-nightly" },
    });
    fireEvent.click(screen.getByRole("button", { name: "关闭设置" }));
    const confirm = screen.getByRole("dialog", {
      name: "放弃未保存的修改？",
    });
    fireEvent.click(within(confirm).getByRole("button", { name: "取消" }));

    expect(close).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue("codex-nightly")).toBeTruthy();
  });
});
