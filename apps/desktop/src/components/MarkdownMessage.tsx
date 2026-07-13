import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
          <code
            {...props}
            className={
              className
                ? `message-code-content ${className}`
                : "message-inline-code"
            }
          >
            {children}
          </code>
        ),
        pre: ({ children }) => <pre className="message-code">{children}</pre>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
