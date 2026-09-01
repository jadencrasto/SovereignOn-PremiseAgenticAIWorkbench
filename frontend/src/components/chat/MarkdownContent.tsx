/**
 * frontend/src/components/chat/MarkdownContent.tsx
 * -------------------------------------------------
 * Rich Markdown renderer supporting GitHub-flavored markdown, code blocks with copy,
 * visual callout alerts (NOTE, TIP, WARNING, IMPORTANT), and native interactive Mermaid diagrams.
 */

import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Check,
  Copy,
  Info,
  AlertTriangle,
  Lightbulb,
  AlertOctagon,
  HelpCircle,
} from 'lucide-react';
import { MermaidRenderer } from './MermaidRenderer';

interface MarkdownContentProps {
  content: string;
}

export const MarkdownContent: React.FC<MarkdownContentProps> = ({ content }) => {
  return (
    <div className="prose-dark overflow-hidden leading-relaxed text-[13.5px] text-slate-200">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code({ className, children, ...props }: any) {
            const match = /language-(\w+)/.exec(className || '');
            const isInline = !match && !String(children).includes('\n');
            const codeString = String(children).replace(/\n$/, '');
            const language = match ? match[1] : '';

            if (isInline) {
              return (
                <code
                  className="px-1.5 py-0.5 rounded bg-slate-800/80 text-sky-300 font-mono text-[12px] border border-slate-700/60"
                  {...props}
                >
                  {children}
                </code>
              );
            }

            // Render Mermaid visual diagram if language is mermaid
            if (language === 'mermaid') {
              return <MermaidRenderer chart={codeString} />;
            }

            return <CodeBlock language={language} code={codeString} />;
          },

          // Custom visual blockquotes (Alert callouts: [!NOTE], [!WARNING], [!TIP])
          blockquote({ children }: any) {
            const childrenText = String(children?.[1]?.props?.children || children?.[0]?.props?.children || '');
            
            if (childrenText.includes('[!NOTE]') || childrenText.includes('[!INFO]')) {
              return (
                <div className="my-3 p-3 rounded-lg bg-sky-950/40 border-l-4 border-sky-500 text-sky-200 flex items-start gap-2.5 text-xs leading-normal">
                  <Info className="w-4 h-4 text-sky-400 shrink-0 mt-0.5" />
                  <div className="flex-1">{children}</div>
                </div>
              );
            }
            if (childrenText.includes('[!WARNING]') || childrenText.includes('[!CAUTION]')) {
              return (
                <div className="my-3 p-3 rounded-lg bg-amber-950/40 border-l-4 border-amber-500 text-amber-200 flex items-start gap-2.5 text-xs leading-normal">
                  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div className="flex-1">{children}</div>
                </div>
              );
            }
            if (childrenText.includes('[!TIP]')) {
              return (
                <div className="my-3 p-3 rounded-lg bg-emerald-950/40 border-l-4 border-emerald-500 text-emerald-200 flex items-start gap-2.5 text-xs leading-normal">
                  <Lightbulb className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                  <div className="flex-1">{children}</div>
                </div>
              );
            }
            if (childrenText.includes('[!IMPORTANT]') || childrenText.includes('[!CRITICAL]')) {
              return (
                <div className="my-3 p-3 rounded-lg bg-rose-950/40 border-l-4 border-rose-500 text-rose-200 flex items-start gap-2.5 text-xs leading-normal">
                  <AlertOctagon className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" />
                  <div className="flex-1">{children}</div>
                </div>
              );
            }

            return (
              <blockquote className="my-3 pl-3.5 border-l-2 border-slate-600 text-slate-300 italic text-xs">
                {children}
              </blockquote>
            );
          },

          // Custom styled tables
          table({ children }: any) {
            return (
              <div className="my-3.5 overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/60 shadow-md">
                <table className="w-full text-left text-xs border-collapse">
                  {children}
                </table>
              </div>
            );
          },
          thead({ children }: any) {
            return <thead className="bg-slate-800/90 border-b border-slate-700 text-slate-200 font-semibold">{children}</thead>;
          },
          tbody({ children }: any) {
            return <tbody className="divide-y divide-slate-800/60 text-slate-300">{children}</tbody>;
          },
          tr({ children }: any) {
            return <tr className="hover:bg-slate-800/40 transition-colors">{children}</tr>;
          },
          th({ children }: any) {
            return <th className="px-3.5 py-2 text-slate-300 font-medium">{children}</th>;
          },
          td({ children }: any) {
            return <td className="px-3.5 py-2 text-slate-300">{children}</td>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
};

const CodeBlock: React.FC<{ language: string; code: string }> = ({ language, code }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy text', err);
    }
  };

  return (
    <div className="relative my-3 rounded-lg border border-slate-800 bg-[#070b14] overflow-hidden text-xs shadow-md shadow-black/40">
      <div className="flex items-center justify-between px-3 py-1.5 bg-slate-900/90 border-b border-slate-800 font-mono text-[11px] text-slate-400">
        <span className="text-sky-400 font-semibold">{language || 'text'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 hover:text-white transition-colors text-[10px] px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-emerald-400" />
              <span className="text-emerald-400 font-medium">Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <div className="p-3 overflow-x-auto bg-[#070b14]">
        <pre className="!bg-transparent !p-0 !m-0 !border-0 font-mono text-slate-200 text-xs leading-relaxed">
          <code>{code}</code>
        </pre>
      </div>
    </div>
  );
};
