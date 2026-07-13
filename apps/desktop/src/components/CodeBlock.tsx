import { useEffect, useMemo, useRef, useState } from "react";
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
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const visibleCode = showAll
    ? code
    : lines.slice(0, COLLAPSED_LINES).join("\n");
  const highlighted = useMemo(() => {
    if (!normalizedLanguage || !hljs.getLanguage(normalizedLanguage))
      return null;
    return hljs.highlight(visibleCode, { language: normalizedLanguage }).value;
  }, [normalizedLanguage, visibleCode]);

  useEffect(
    () => () => {
      if (resetTimer.current) clearTimeout(resetTimer.current);
    },
    [],
  );

  const copyCode = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    if (resetTimer.current) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="message-code">
      <span className="message-code-language">
        {normalizedLanguage || "text"}
      </span>
      <button
        className="message-code-copy"
        type="button"
        onClick={() => void copyCode()}
        aria-label={copied ? "代码已复制" : "复制代码"}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? "已复制" : "复制"}
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
