import React, { useState, useEffect } from "react";
import { Check, Copy, Terminal } from "lucide-react";
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
      if (grammar) {
        setHighlightedCode(Prism.highlight(code, grammar, cleanLang));
      } else {
        setHighlightedCode(code);
      }
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
    <div className="my-3 rounded-xl overflow-hidden border border-white/10 bg-[#090C15] shadow-lg">
      {/* Code Header Bar */}
      <div className="flex items-center justify-between px-3.5 py-1.5 bg-[#0D1220] border-b border-white/[0.08] select-none text-xs">
        <div className="flex items-center gap-1.5 text-slate-400 font-mono font-medium">
          <Terminal className="w-3.5 h-3.5 text-indigo-400" />
          <span>{cleanLang.toUpperCase()}</span>
        </div>
        <button
          onClick={copyCode}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-slate-400 hover:text-slate-200 hover:bg-white/[0.06] transition"
        >
          {copied ? (
            <>
              <Check className="w-3.5 h-3.5 text-emerald-400" />
              <span className="text-emerald-400">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="w-3.5 h-3.5" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>

      {/* Code Content */}
      <pre className="p-3.5 overflow-x-auto font-mono text-[13px] leading-relaxed">
        <code
          className={`language-${cleanLang}`}
          dangerouslySetInnerHTML={{ __html: highlightedCode }}
        />
      </pre>
    </div>
  );
};

