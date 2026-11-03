import React, { useState, useEffect } from "react";
import { Check, Copy } from "lucide-react";
import Prism from "prismjs";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-typescript";
import "prismjs/components/prism-python";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-json";
import "prismjs/components/prism-sql";
import "prismjs/components/prism-rust";
import "prismjs/components/prism-c";
import "prismjs/components/prism-cpp";
import "prismjs/components/prism-markdown";

interface CodeBlockProps {
  language: string;
  code: string;
}

export const CodeBlock: React.FC<CodeBlockProps> = ({ language, code }) => {
  const [copied, setCopied] = useState(false);
  const [highlightedCode, setHighlightedCode] = useState(code);

  const cleanLang = (language || "text").toLowerCase().trim();

  useEffect(() => {
    try {
      const grammar = Prism.languages[cleanLang] || Prism.languages.text;
      setHighlightedCode(grammar ? Prism.highlight(code, grammar, cleanLang) : code);
    } catch {
      setHighlightedCode(code);
    }
  }, [code, cleanLang]);

  const copyCode = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="my-3.5 rounded-2xl overflow-hidden border border-line/70 bg-panel/85 backdrop-blur-md shadow-md not-prose">
      <div className="flex items-center justify-between h-8 px-3.5 border-b border-line/50 bg-surface/80 select-none text-xs font-mono text-muted">
        <span className="font-semibold text-accent/90">{cleanLang === "text" ? "" : cleanLang}</span>
        <button
          onClick={copyCode}
          className="flex items-center gap-1.5 h-6 px-2 rounded-lg text-xs font-sans text-subtle hover:text-fg hover:bg-raised/80 hover:border-line/60 border border-transparent transition-all"
        >
          {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="p-3.5 overflow-x-auto font-mono text-[13px] leading-relaxed bg-canvas/70">
        <code className={`language-${cleanLang}`} dangerouslySetInnerHTML={{ __html: highlightedCode }} />
      </pre>
    </div>
  );
};
