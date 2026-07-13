import { useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { useCopyFeedback } from "../hooks/useCopyFeedback";

const COLLAPSE_AFTER_LINES = 400;
const COLLAPSED_LINES = 80;

const languages = [
  ["typescript", typescript],
  ["ts", typescript],
  ["tsx", typescript],
  ["javascript", javascript],
  ["js", javascript],
  ["jsx", javascript],
  ["python", python],
  ["py", python],
  ["rust", rust],
  ["bash", bash],
  ["sh", bash],
  ["shell", bash],
  ["json", json],
  ["diff", diff],
  ["css", css],
  ["html", xml],
  ["xml", xml],
  ["sql", sql],
  ["yaml", yaml],
  ["yml", yaml],
  ["markdown", markdown],
  ["md", markdown],
] as const;

for (const [name, definition] of languages) {
  hljs.registerLanguage(name, definition);
}

export function CodeBlock({
  code,
  language,
}: {
  code: string;
  language?: string;
}) {
  const normalizedLanguage = language?.toLowerCase();
  const lines = code.split("\n");
  const collapsible = lines.length > COLLAPSE_AFTER_LINES;
  const [expanded, setExpanded] = useState(false);
  const showAll = expanded || !collapsible;
  const { copyState, copyText } = useCopyFeedback();
  const visibleCode = showAll
    ? code
    : lines.slice(0, COLLAPSED_LINES).join("\n");
  const highlighted = useMemo(() => {
    if (!normalizedLanguage || !hljs.getLanguage(normalizedLanguage))
      return null;
    return hljs.highlight(visibleCode, { language: normalizedLanguage }).value;
  }, [normalizedLanguage, visibleCode]);

  const copyLabel =
    copyState === "copied"
      ? "已复制"
      : copyState === "failed"
        ? "复制失败"
        : "复制";

  return (
    <div className="message-code">
      <span className="message-code-language">
        {normalizedLanguage || "text"}
      </span>
      <button
        className="message-code-copy"
        type="button"
        onClick={() => void copyText(code)}
        aria-label={copyState === "copied" ? "代码已复制" : `${copyLabel}代码`}
      >
        {copyState === "copied" ? <Check size={12} /> : <Copy size={12} />}
        {copyLabel}
      </button>
      <pre>
        {highlighted === null ? (
          <code>{visibleCode}</code>
        ) : (
          <code
            className={`hljs language-${normalizedLanguage}`}
            dangerouslySetInnerHTML={{ __html: highlighted }}
          />
        )}
      </pre>
      {!showAll && (
        <button
          className="message-code-expand"
          type="button"
          onClick={() => setExpanded(true)}
        >
          展开全部（{lines.length} 行）
        </button>
      )}
    </div>
  );
}
