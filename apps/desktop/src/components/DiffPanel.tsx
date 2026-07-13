import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Code2, FileCode2, LoaderCircle } from "lucide-react";

const MonacoEditor = lazy(async () => {
  const [reactMonaco, monaco] = await Promise.all([
    import("@monaco-editor/react"),
    import("monaco-editor"),
  ]);
  reactMonaco.loader.config({ monaco });
  return { default: reactMonaco.default };
});

export interface FileDiff {
  path: string;
  content: string;
}

export function splitGitDiff(diff: string): FileDiff[] {
  const starts = Array.from(diff.matchAll(/^diff --git a\/(.+?) b\/(.+)$/gm));
  if (!starts.length)
    return diff.trim() ? [{ path: "完整变更", content: diff }] : [];

  return starts.map((match, index) => ({
    path: match[2].trim(),
    content: diff.slice(match.index, starts[index + 1]?.index ?? diff.length),
  }));
}

export function DiffPanel({ diff }: { diff: string }) {
  const files = useMemo(() => splitGitDiff(diff), [diff]);
  const [selectedPath, setSelectedPath] = useState("");
  const selected = files.find((file) => file.path === selectedPath) ?? files[0];

  useEffect(() => {
    if (files.length && !files.some((file) => file.path === selectedPath)) {
      setSelectedPath(files[0].path);
    }
  }, [files, selectedPath]);

  if (!selected) {
    return (
      <div className="diff-panel">
        <div className="panel-empty">
          <Code2 size={22} />
          <strong>暂无 Diff</strong>
          <span>Codex 修改文件后，这里会显示真实 Git Diff。</span>
        </div>
      </div>
    );
  }

  return (
    <div className="diff-panel has-files">
      <nav className="diff-files" aria-label="变更文件">
        <header>{files.length} 个文件</header>
        {files.map((file) => (
          <button
            className={file.path === selected.path ? "active" : ""}
            key={file.path}
            onClick={() => setSelectedPath(file.path)}
            title={file.path}
          >
            <FileCode2 size={13} />
            <span>{file.path}</span>
          </button>
        ))}
      </nav>
      <div className="diff-editor">
        <Suspense
          fallback={
            <div className="panel-loading">
              <LoaderCircle className="spin" size={16} /> 正在加载 Diff 查看器…
            </div>
          }
        >
          <MonacoEditor
            height="100%"
            language="diff"
            theme="vs-dark"
            options={{
              readOnly: true,
              minimap: { enabled: false },
              fontSize: 12,
              scrollBeyondLastLine: false,
              wordWrap: "on",
              padding: { top: 12 },
            }}
            value={selected.content}
          />
        </Suspense>
      </div>
    </div>
  );
}
