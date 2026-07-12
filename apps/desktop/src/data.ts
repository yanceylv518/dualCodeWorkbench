import type { Workspace } from "./types";

export const demo: Workspace[] = [
  {
    id: "w1",
    name: "DualCode Workbench 示例",
    path: "D:/Projects/dualcode",
    threads: [
      {
        id: "t1",
        title: "实现协作执行状态机",
        state: "COMPLETED",
        messages: [
          { id: "1", agent: "user", text: "请实现协作执行状态机，并补充测试。", time: "10:21" },
          { id: "2", agent: "claude", text: "计划包含状态定义、合法迁移校验和事件审计。", time: "10:22" },
          { id: "3", agent: "codex", text: "已实现状态机与 WebSocket 事件。", time: "10:24" },
          { id: "4", agent: "system", text: "12 tests passed in 0.84s", time: "10:25" },
        ],
      },
    ],
  },
];
