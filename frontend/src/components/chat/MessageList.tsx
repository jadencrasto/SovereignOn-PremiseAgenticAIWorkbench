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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col justify-center p-8 max-w-5xl mx-auto select-none space-y-6">
        {/* Header Block */}
        <div className="border-2 border-[#cbd5e1] bg-white text-[#0f172a] p-6 brutal-shadow-blue">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 border-b-2 border-[#f1f5f9] pb-3 mb-3">
            <div className="font-display font-black text-2xl tracking-tighter uppercase text-[#0f172a]">
              SOVEREIGN // INDUSTRIAL AGENT TERMINAL
            </div>
            <span className="font-mono text-xs font-black px-2.5 py-1 bg-[#0284c7] text-white uppercase self-start md:self-auto">
              ZERO-EGRESS &bull; 100% LOCAL
            </span>
          </div>
          <p className="font-mono text-xs font-bold leading-relaxed text-slate-600 max-w-3xl">
            AUTONOMOUS ON-PREMISE ENGINE FOR REFINERY QA, MECHANICAL NDT CORROSION ANALYSIS, AND EMERGENCY INTERLOCK DISPATCH. ALL INFERENCE EXCLUSIVELY ON THIS HOST.
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
            className="border-2 border-[#cbd5e1] bg-white p-5 cursor-pointer brutal-btn hover:border-[#0284c7] brutal-shadow-blue flex flex-col justify-between space-y-4"
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-2xl font-black font-display text-[#0284c7]">01</span>
                <span className="text-[10px] font-bold px-1.5 py-0.5 bg-[#e0f2fe] text-[#0369a1] uppercase border border-[#bae6fd]">
                  CSV &rarr; XLSX
                </span>
              </div>
              <h3 className="font-display font-black text-sm uppercase text-[#0f172a] tracking-tight">
                Hydrocarbon Chemical QA
              </h3>
              <p className="font-sans text-xs text-slate-600 leading-normal">
                Cross-reference batch composition vs MRPL limit standard. Compute tolerances and build verified Excel artifact.
              </p>
            </div>
            <div className="pt-3 border-t-2 border-[#f1f5f9] flex items-center justify-between text-xs font-bold text-[#0284c7]">
              <span>[RUN PROCEDURE]</span>
              <ArrowUpRight className="w-4 h-4" />
            </div>
          </div>

          {/* Card 02 */}
          <div
            onClick={() =>
              onSelectPrompt?.(
                "Analyze the inspection image of valve MOV-4102-B in the Desalter Unit. Identify visible corrosion defects, cross-reference with our equipment maintenance manual, and produce an inspection advisory with recommended remedial action."
              )
            }
            className="border-2 border-[#cbd5e1] bg-white p-5 cursor-pointer brutal-btn hover:border-[#2563eb] brutal-shadow-sky flex flex-col justify-between space-y-4"
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-2xl font-black font-display text-[#2563eb]">02</span>
                <span className="text-[10px] font-bold px-1.5 py-0.5 bg-[#dbeafe] text-[#1d4ed8] uppercase border border-[#bfdbfe]">
                  VLM &rarr; SOP
                </span>
              </div>
              <h3 className="font-display font-black text-sm uppercase text-[#0f172a] tracking-tight">
                Visual NDT Valve Inspection
              </h3>
              <p className="font-sans text-xs text-slate-600 leading-normal">
                Examine valve MOV-4102-B photograph for pitting corrosion, query mechanical manual, and generate remedial advisory.
              </p>
            </div>
            <div className="pt-3 border-t-2 border-[#f1f5f9] flex items-center justify-between text-xs font-bold text-[#2563eb]">
              <span>[RUN PROCEDURE]</span>
              <ArrowUpRight className="w-4 h-4" />
            </div>
          </div>

          {/* Card 03 */}
          <div
            onClick={() =>
              onSelectPrompt?.(
                "Alert: Pressure transmitter PT-4011 on Flare Knock-Out Drum FKOD-101 has spiked to 2.85 bar gauge. Check the standard emergency operating procedure, list immediate interlock actions, and draft the control room incident dispatch log."
              )
            }
            className="border-2 border-[#cbd5e1] bg-white p-5 cursor-pointer brutal-btn hover:border-[#d97706] brutal-shadow-yellow flex flex-col justify-between space-y-4"
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-2xl font-black font-display text-[#d97706]">03</span>
                <span className="text-[10px] font-bold px-1.5 py-0.5 bg-[#fef3c7] text-[#92400e] uppercase border border-[#fde68a]">
                  EMERGENCY
                </span>
              </div>
              <h3 className="font-display font-black text-sm uppercase text-[#0f172a] tracking-tight">
                Flare Drum Pressure Anomaly
              </h3>
              <p className="font-sans text-xs text-slate-600 leading-normal">
                Process transmitter spike, retrieve flare system runbook, verify interlock steps, and log control room dispatch.
              </p>
            </div>
            <div className="pt-3 border-t-2 border-[#f1f5f9] flex items-center justify-between text-xs font-bold text-[#d97706]">
              <span>[RUN PROCEDURE]</span>
              <ArrowUpRight className="w-4 h-4" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4 font-mono bg-[#f0f7ff]">
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
