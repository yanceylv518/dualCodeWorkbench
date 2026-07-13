import { isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "./CodeBlock";

function renderCodeBlock(children: ReactNode) {
  const child = Array.isArray(children) ? children[0] : children;
  if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) {
    return <CodeBlock code={String(children ?? "")} />;
  }
  const language = child.props.className?.match(/language-([\w-]+)/)?.[1];
  const code = String(child.props.children ?? "").replace(/\n$/, "");
  return <CodeBlock code={code} language={language} />;
}

export function MarkdownMessage({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={{
        a: ({ children, ...props }) => (
          <a {...props} target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
        code: ({ className, children, ...props }) => (
          <code {...props} className={className || "message-inline-code"}>
            {children}
          </code>
        ),
        pre: ({ children }) => renderCodeBlock(children),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
