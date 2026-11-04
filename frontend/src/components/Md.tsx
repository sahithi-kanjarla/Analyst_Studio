import { Children, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { mdSchema } from "./mdSchema";

const NUMERIC = /^[-+−]?\s?[\d.,]*\d[\d.,]*\s?(%|[KMB]|×)?$/;
const text = (c: ReactNode) => Children.toArray(c).map((x) => (typeof x === "string" || typeof x === "number" ? String(x) : "")).join("").replace(/\*/g, "").trim();
/** True for a cell that is just a number (with %, K, M or B), so it can be right-aligned. */
export const isNumeric = (c: ReactNode) => NUMERIC.test(text(c));

/** Markdown with the app's table style: numbers right-aligned. */
export default function Md({ md }: { md: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, mdSchema]]}
        components={{
          td: ({ children }) => <td className={isNumeric(children) ? "num" : undefined}>{children}</td>,
          th: ({ children }) => <th className={isNumeric(children) ? "num" : undefined}>{children}</th>,
        }}
      >
        {md}
      </ReactMarkdown>
    </div>
  );
}
