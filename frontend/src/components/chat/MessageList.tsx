/**
 * frontend/src/components/chat/MessageList.tsx
 * --------------------------------------------
 * Industrial Terminal Stream & Dispatch Matrix (White & Light Blue Style)
 */

import React, { useEffect, useRef } from 'react';
import type { ChatMessage } from '../../types';
import { MessageItem } from './MessageItem';
import { ArrowUpRight } from 'lucide-react';

interface MessageListProps {
  messages: ChatMessage[];
  onSelectPrompt?: (prompt: string) => void;
  onRetry?: (content: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
}

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  onSelectPrompt,
  onRetry,
  onApprove,
  onReject,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef<boolean>(true);

  // Track whether user is near bottom of scroll
  const handleScroll = () => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const threshold = 150;
    isNearBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  };

  // Auto-scroll: instant during streaming for reliable tracking, smooth otherwise
  useEffect(() => {
    if (!isNearBottomRef.current || !scrollContainerRef.current) return;
    const el = scrollContainerRef.current;
    const isStreaming = messages.some((m) => m.isStreaming);
    if (isStreaming) {
      // Instant scroll keeps up with rapid streaming deltas
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    } else {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col justify-center p-8 max-w-5xl mx-auto select-none space-y-6">
        {/* Header Block */}
        <div className="border border-slate-200 rounded-xl bg-white text-slate-800 p-8 shadow-sm">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b border-slate-100 pb-4 mb-4">
            <div className="font-display font-semibold text-2xl tracking-tight text-slate-900">
              Sovereign Assistant
            </div>
            <span className="font-sans text-xs font-semibold px-3 py-1 bg-blue-50 text-blue-600 rounded-full border border-blue-100 self-start md:self-auto">
              Secure &bull; Local
            </span>
          </div>
          <p className="font-sans text-sm font-medium leading-relaxed text-slate-600 max-w-3xl">
            Autonomous agent for analysis, data QA, and intelligent workflows. All inference runs securely on your local machine.
          </p>
        </div>

        {/* 3 Dispatch Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 font-mono">
          {/* Card 01 */}
          <div
            onClick={() =>
              onSelectPrompt?.(
                "Read the lab dataset 'mrpl_lab_composition_test.csv' and cross-check the chemical composition values against our internal refinery quality specifications. Identify all deviations exceeding maximum allowable thresholds, calculate the percentage variance for each, and generate a styled compliance report 'mrpl_chemical_compliance_report.xlsx' with pass/fail conditional formatting. Finally, verify the generated report."
              )
            }
            className="border border-slate-200 rounded-xl bg-white p-6 cursor-pointer hover:border-blue-300 hover:shadow-md transition-all shadow-sm flex flex-col justify-between space-y-4"
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xl font-bold font-display text-blue-500">Data QA</span>
                <span className="text-[10px] font-semibold px-2 py-1 rounded-full bg-blue-50 text-blue-600 border border-blue-100">
                  CSV to XLSX
                </span>
              </div>
              <h3 className="font-display font-semibold text-base text-slate-800">
                Data Quality Assurance
              </h3>
              <p className="font-sans text-sm text-slate-600 leading-normal">
                Cross-reference data against standards. Compute tolerances and build verified Excel artifacts.
              </p>
            </div>
            <div className="pt-4 border-t border-slate-100 flex items-center justify-between text-xs font-semibold text-blue-600 group">
              <span>Run Example</span>
              <ArrowUpRight className="w-4 h-4 transition-transform group-hover:translate-x-1 group-hover:-translate-y-1" />
            </div>
          </div>

          {/* Card 02 */}
          <div
            onClick={() =>
              onSelectPrompt?.(
                "Analyze the inspection image of valve MOV-4102-B in the Desalter Unit. Identify visible corrosion defects, cross-reference with our equipment maintenance manual, and produce an inspection advisory with recommended remedial action."
              )
            }
            className="border border-slate-200 rounded-xl bg-white p-6 cursor-pointer hover:border-blue-400 hover:shadow-md transition-all shadow-sm flex flex-col justify-between space-y-4"
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xl font-bold font-display text-blue-600">Visual</span>
                <span className="text-[10px] font-semibold px-2 py-1 rounded-full bg-indigo-50 text-indigo-600 border border-indigo-100">
                  Image Analysis
                </span>
              </div>
              <h3 className="font-display font-semibold text-base text-slate-800">
                Image Inspection
              </h3>
              <p className="font-sans text-sm text-slate-600 leading-normal">
                Examine images for defects, query manual, and generate remedial advisory.
              </p>
            </div>
            <div className="pt-4 border-t border-slate-100 flex items-center justify-between text-xs font-semibold text-blue-600 group">
              <span>Run Example</span>
              <ArrowUpRight className="w-4 h-4 transition-transform group-hover:translate-x-1 group-hover:-translate-y-1" />
            </div>
          </div>

          {/* Card 03 */}
          <div
            onClick={() =>
              onSelectPrompt?.(
                "Alert: Pressure transmitter PT-4011 on Flare Knock-Out Drum FKOD-101 has spiked to 2.85 bar gauge. Check the standard emergency operating procedure, list immediate interlock actions, and draft the control room incident dispatch log."
              )
            }
            className="border border-slate-200 rounded-xl bg-white p-6 cursor-pointer hover:border-amber-400 hover:shadow-md transition-all shadow-sm flex flex-col justify-between space-y-4"
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xl font-bold font-display text-amber-500">Emergency</span>
                <span className="text-[10px] font-semibold px-2 py-1 rounded-full bg-amber-50 text-amber-600 border border-amber-100">
                  Critical
                </span>
              </div>
              <h3 className="font-display font-semibold text-base text-slate-800">
                Incident Response
              </h3>
              <p className="font-sans text-sm text-slate-600 leading-normal">
                Process metrics, retrieve runbooks, verify interlock steps, and draft dispatch logs.
              </p>
            </div>
            <div className="pt-4 border-t border-slate-100 flex items-center justify-between text-xs font-semibold text-amber-500 group">
              <span>Run Example</span>
              <ArrowUpRight className="w-4 h-4 transition-transform group-hover:translate-x-1 group-hover:-translate-y-1" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={scrollContainerRef}
      onScroll={handleScroll}
      className="flex-1 min-h-0 overflow-y-auto px-4 py-6 pb-8 space-y-4 bg-slate-50 font-sans"
    >
      {messages.map((message) => (
        <MessageItem
          key={message.id}
          message={message}
          onRetry={onRetry}
          onApprove={onApprove}
          onReject={onReject}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
};
