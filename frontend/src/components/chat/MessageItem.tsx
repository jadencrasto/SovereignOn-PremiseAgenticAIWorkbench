/**
 * frontend/src/components/chat/MessageItem.tsx
 * --------------------------------------------
 * Industrial Terminal Message Item (White & Light Blue Style)
 */

import React, { useState } from 'react';
import type { ChatMessage, LocalDocumentLocator, ToolEvent } from '../../types';

function documentPosition(locator: LocalDocumentLocator): string {
  if (locator.format === 'xlsx') {
    return `${locator.sheet} · ${locator.cell ? `cell ${locator.cell}` : `range ${locator.range}`}`;
  }
  const position = locator.table !== undefined
    ? `table ${locator.table}, row ${locator.row}${locator.column !== undefined ? `, column ${locator.column}` : ''}`
    : locator.format === 'pdf' ? `block ${locator.block}` : `paragraph ${locator.paragraph}`;
  return locator.format === 'pdf' ? `page ${locator.page} · ${position}` : position;
}
import { MarkdownContent } from './MarkdownContent';
import { SourceCard } from './SourceCard';
import { Copy, Check, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { PlanTimeline } from '../agent/PlanTimeline';
import { ApprovalCard } from '../agent/ApprovalCard';

interface MessageItemProps {
  message: ChatMessage;
  onRetry?: (content: string) => void;
  onApprove?: (taskId: string) => void;
  onReject?: (taskId: string) => void;
}

const ToolActivity: React.FC<{ events: ToolEvent[]; isStreaming?: boolean }> = ({ events, isStreaming }) => {
  const [expanded, setExpanded] = useState(false);
  if (!events || events.length === 0) return null;

  const completedTools = events.filter((e) => e.type === 'tool_result');
  // Match starts and results in order: the same tool may be invoked repeatedly.
  const outstanding = new Map<string, number>();
  for (const event of events) {
    const count = outstanding.get(event.tool) || 0;
    if (event.type === 'tool_start') outstanding.set(event.tool, count + 1);
    else if (event.type === 'tool_result' && count > 0) outstanding.set(event.tool, count - 1);
  }
  const runningTool = [...outstanding.values()].some(count => count > 0);
  const unknownResult = completedTools.some(event => event.success !== true && event.success !== false);

  const formatToolName = (tool: string) => {
    switch (tool) {
      case 'document_search':
        return 'BENCHMARK_RAG_SEARCH';
      case 'file_read':
        return 'WORKSPACE_FILE_INGEST';
      case 'calculator':
        return 'Calculator';
      case 'unit_conversion':
        return 'Unit conversion';
      case 'xlsx_report':
        return 'XLSX_COMPLIANCE_BUILDER';
      case 'file_write':
        return 'SANDBOX_FILE_WRITE';
      case 'artifact_verifier':
        return 'CRYPTO_SHA256_VERIFIER';
      default:
        return tool.toUpperCase();
    }
  };

  return (
    <div className="mb-4 border border-blue-100 rounded-lg bg-blue-50/30 font-sans text-xs">
      <button type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        className="w-full flex items-center justify-between p-3 bg-blue-50/50 border-b border-blue-100 cursor-pointer hover:bg-blue-100/50 transition-colors rounded-t-lg"
      >
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 bg-blue-500 text-white font-semibold text-[10px] rounded-full">
            {runningTool ? (isStreaming ? 'Running' : 'Outcome uncertain') : completedTools.some(e => e.success === false) ? 'Finished with errors' : unknownResult ? 'Outcome unknown' : 'Results received'}
          </span>
          <span className="font-semibold text-blue-700 tracking-tight">
            Tool Pipeline ({completedTools.length} actions)
          </span>
        </div>
        <span className="text-[10px] font-medium text-blue-600">
          {expanded ? 'Hide Logs' : 'View Logs'}
        </span>
      </button>

      {!expanded && completedTools.length > 0 && (
        <div className="p-2.5 flex flex-wrap gap-2 bg-blue-50/30 rounded-b-lg">
          {completedTools.map((ev, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1.5 px-2 py-1 bg-white border border-blue-100 rounded-md text-[11px] font-medium text-blue-700 shadow-sm"
            >
              {ev.success !== true ? <AlertTriangle className="w-3.5 h-3.5 text-red-600" /> : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />}
              <span>{formatToolName(ev.tool)}</span>
            </span>
          ))}
        </div>
      )}

      {expanded && (
        <div className="p-3 space-y-2 bg-white rounded-b-lg border-t border-blue-100">
          {events.map((event, idx) => (
            <div key={idx} className="flex items-start gap-2 text-[11px]">
              {event.type === 'tool_start' ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-blue-500 mt-1 shrink-0" />
                  <div>
                    <span className="font-semibold text-blue-600">
                      Running {formatToolName(event.tool)}
                    </span>
                  </div>
                </>
              ) : (
                <>
                  {event.success !== true ? <AlertTriangle className="w-3.5 h-3.5 text-red-600 shrink-0 mt-0.5" /> : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0 mt-0.5" />}
                  <div>
                    <span className="font-semibold text-slate-700">
                      {event.success === true ? 'Completed' : event.success === false ? 'Failed' : 'Outcome unknown for'} {formatToolName(event.tool)}
                    </span>
                    {event.summary && (
                      <span className="ml-2 text-slate-600">{event.summary}</span>
                    )}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

function FileAnswer({ message }: { message: ChatMessage }) {
  const deterministic = !message.error && !message.isStreaming && message.model_used === 'Deterministic file analysis';
  const filename = message.fileName;
  // The backend emits one final literal text block headed by the actual upload name.
  // Preserve its body as text, without inferring column or citation metadata from prose.
  const block = deterministic && filename && !/[\\/\r\n]/.test(filename)
    ? /\n\n(`{3,})text\n([\s\S]+)\n\1$/.exec(message.content) : null;
  const fileHeader = 'File: ' + filename;
  const evidence = block && (block[2] === fileHeader || block[2].startsWith(fileHeader + '\n'))
    ? block[2].slice(fileHeader.length).replace(/^\n/, '') : null;
  const result = evidence !== null && block ? message.content.slice(0, block.index) : message.content;
  const emphasis = deterministic && evidence !== null ? /^\*\*([^*\n]+)\*\*/.exec(result) : null;
  return (
    <div className="min-w-0 space-y-3">
      {!deterministic && !message.error && !message.isStreaming && message.model_used && (
        <p className="text-xs text-slate-600">Model response without verified file citations.</p>
      )}
      <p className="whitespace-pre-wrap text-sm [overflow-wrap:anywhere]">
        {emphasis ? <><strong>{emphasis[1]}</strong>{result.slice(emphasis[0].length)}</> : result}
      </p>
      {evidence && (
        <section aria-label="Verified source evidence" className="min-w-0 rounded-lg border border-slate-200 bg-slate-50">
          <h3 className="border-b border-slate-200 px-3 py-2 text-xs font-semibold text-slate-700">Verified source evidence</h3>
          <pre tabIndex={0} aria-label={filename?.toLowerCase().endsWith('.json') ? 'Source record indices and field paths' : 'Source columns, rows or lines'}
            className="max-h-72 max-w-full overflow-auto whitespace-pre-wrap p-3 text-xs leading-relaxed [overflow-wrap:anywhere]">
            {evidence}
          </pre>
        </section>
      )}
    </div>
  );
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, onRetry, onApprove, onReject }) => {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === 'user';

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy', err);
    }
  };

  if (isUser) {
    return (
      <div className="flex justify-end gap-3 max-w-5xl mx-auto px-2 py-2 font-sans">
        <div className="flex flex-col items-end max-w-[85%]">
          <div className="flex items-center gap-2 mb-1 text-[10px] font-medium text-slate-500">
            <span>{message.timestamp}</span>
            <span className="px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-600 border border-blue-100">User</span>
          </div>

          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-2 flex flex-col items-end gap-1.5 w-full">
              {message.attachments.map((att) => (
                <div key={att.id} className="max-w-xs border border-slate-200 rounded-lg bg-white p-1.5 shadow-sm">
                  {att.objectUrl ? <img
                    src={att.objectUrl}
                    alt={att.filename}
                    className="w-full max-h-48 object-cover rounded-md border border-slate-100"
                  /> : <p className="px-2 text-xs text-slate-500">
                    {att.mimeType === 'text/csv' ? 'CSV · Attach again to ask about it' :
                      att.mimeType === 'application/json' ? 'JSON · Attach again to ask about it' :
                      att.mimeType === 'text/plain' ? 'Text file · Attach again to ask about it' : att.localAvailable === false ? 'Image removed or unavailable for reuse' : 'Image preview unavailable in history'}
                  </p>}
                  <div className="flex items-center justify-between px-2 py-1.5 text-xs font-medium text-slate-700">
                    <span className="max-w-[180px] break-all">
                      {att.sourceNumber ? 'Source ' + att.sourceNumber + ' · ' : ''}{att.filename}
                    </span>
                    <span className="text-slate-500">{(att.sizeBytes / 1024).toFixed(0)} KB</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="p-4 rounded-2xl rounded-tr-sm bg-blue-500 text-white font-sans text-sm leading-relaxed shadow-sm">
            <p className="whitespace-pre-wrap">{message.content}</p>
          </div>
          {message.localTurn && message.localTurn.state !== 'completed' && (
            <p role="status" className="mt-1 text-xs text-slate-600">
              {message.localTurn.state === 'failed' ? ({
                OLLAMA_UNAVAILABLE: 'Ollama is unavailable. Start it and retry.',
                MODEL_UNAVAILABLE: 'The selected local model is unavailable.',
                MODEL_TIMEOUT: 'Ollama timed out.',
                LOCAL_VISION_MODEL_UNAVAILABLE: 'The selected local vision model is unavailable. Check Ollama and the Models panel.',
                LOCAL_VISION_FAILED: 'The local vision model could not complete this image analysis.',
                LOCAL_IMAGE_UNAVAILABLE: 'This image is unavailable. Attach an image to start a new question.',
                LOCAL_DRAFT_EVIDENCE: 'Choose current findings from this conversation before requesting a draft.',
                LOCAL_DRAFT_FAILED: 'The draft could not be assembled within the local limits.',
                MODEL_INCOMPLETE: 'Ollama returned an incomplete or invalid response.',
                LOCAL_TOOL_UNAUTHORIZED: 'Local chat can run arithmetic and unit conversions only.',
                LOCAL_TOOL_APPROVAL_REQUIRED: 'This operation requires approval and is unavailable in local chat.',
                LOCAL_TOOL_CONFIRMATION_REQUIRED: 'This tool requires confirmation and is unavailable in local chat.',
                LOCAL_TOOL_UNAVAILABLE: 'The requested local tool is unavailable.',
                LOCAL_TOOL_UNSUPPORTED: 'Try writing an explicit calculation.',
                LOCAL_TOOL_INVALID: 'The numeric request is invalid or outside the supported bounds.',
                LOCAL_TOOL_FAILED: 'The tool could not produce a verified result.',
                LOCAL_TOOL_CHANGED_PLAN: 'The proposed plan changed. Send a new message to start again.',
                LOCAL_TOOL_CONTEXT: 'Specify one verified result, such as the result from the first calculation, or enter the value.',
                LOCAL_TOOL_REFERENCE_UNIT: 'Give both measurements a unit for addition or subtraction, such as 5 m or 5 km.',
                LOCAL_TOOL_DIMENSION: 'Use compatible dimensions for addition and subtraction, or multiply or divide a measurement by a unitless number.',
                LOCAL_TOOL_TEMPERATURE: 'That temperature operation is unsupported. Use Δ°C, Δ°F or ΔK for a difference. To find the difference between two readings, subtract them.',
                LOCAL_TOOL_ABSOLUTE_ZERO: 'An absolute temperature cannot be below absolute zero (0 K). Check the reading or temperature change.',
                LOCAL_TOOL_TARGET_UNIT: 'Which unit should I convert the verified result to?',
                LOCAL_TOOL_UNIT: 'Use supported units of the same dimension. Specify US customary volume units.',
                LOCAL_TOOL_ZERO_DIVISION: 'Division by zero is undefined.',
                LOCAL_KNOWLEDGE_QUESTION: 'Ask a local knowledge question of 1 to 600 characters with at most four parts.',
                LOCAL_KNOWLEDGE_CHANGED: 'Selected local sources changed. Submit a new question with current sources.',
              } as Record<string, string>)[message.localTurn.error_code || ''] || 'The response could not be completed.'
                : { running: 'Response running', cancelled: 'Response cancelled', waiting_clarification: 'Waiting for your choice',
                    interrupted: 'Response interrupted' }[message.localTurn.state]}
            </p>
          )}
          {message.localTurn?.workflow_progress?.length && !message.withheld ? (
            <p className="mt-1 text-xs text-slate-600">
              {'Completed work: '}{message.localTurn.workflow_progress.map(step => ({
                retrieve: 'source retrieval', compare: 'comparison', vision: 'image analysis', evidence: 'evidence preparation',
              })[step]).join(', ')}
            </p>
          ) : null}
          {message.localTurn?.knowledge_scope && !message.withheld && (
            <p className="mt-2 break-words text-xs text-slate-600">
              {message.localTurn.knowledge_scope.mode === 'all' ? 'All local sources at submission' : 'Selected local sources'}
              {' · '}{message.localTurn.knowledge_scope.sources.map(source => `${source.label} (rev. ${source.revision})`).join(', ') || 'Empty corpus'}
              {message.localTurn.knowledge_scope_stale && ' · A pinned revision is unavailable'}
            </p>
          )}
        </div>
      </div>
    );
  }

  // Assistant Message (White & Light Blue Terminal Output)
  const hasToolEvents = !message.withheld && message.toolEvents && message.toolEvents.length > 0;
  const plan = !message.withheld && message.plan && message.plan.steps.length > 0 ? (
    <PlanTimeline taskId={message.plan.taskId} objective={message.plan.objective} steps={message.plan.steps}
      fileAnalysis={message.textFileAnswer} ended={!message.isStreaming} />
  ) : null;

  return (
    <div className="flex flex-col gap-2 max-w-5xl mx-auto px-2 py-3 font-sans">
      {/* Meta Header */}
      <div className="flex items-center justify-between w-full text-xs font-medium border-b border-slate-200 pb-2 mb-1">
        <div className="flex items-center gap-2">
          <span className="px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 font-semibold text-[10px]">
            Assistant
          </span>
          {message.model_used && (
            <span className="px-2 py-0.5 rounded-full bg-blue-50 border border-blue-100 text-blue-600 text-[10px]">
              {message.model_used}
            </span>
          )}
        </div>
        <span className="text-[10px] text-slate-500 font-bold">{message.timestamp}</span>
      </div>

      {/* Main Terminal Output Box */}
      <div
        className={`w-full p-6 rounded-xl bg-white border shadow-sm ${
          message.error
            ? 'border-rose-200 text-rose-700'
            : 'border-slate-200 text-slate-800'
        }`}
      >
        {message.textFileAnswer && !message.withheld && (
          <div className="mb-3 text-xs text-slate-600">
            {message.fileSources?.length ? (
              <ul className="mb-1 font-semibold text-slate-800 [overflow-wrap:anywhere]" aria-label="Compared file sources">
                {message.fileSources.map(source => <li key={source.attachment_id}>
                  {source.role === 'limits' ? 'Limits' : 'Measurements'} · Source {source.attachment_id.slice(7)} · {source.filename}
                </li>)}
              </ul>
            ) : message.fileName && <p className="mb-1 font-semibold text-slate-800 [overflow-wrap:anywhere]">
              {message.fileSource ? 'Source ' + message.fileSource + ' · ' : ''}{message.fileName}
            </p>}
            <p>File results are available in this view only.</p>
          </div>
        )}
        {message.withheld ? (
          <p role="status" className="text-sm text-slate-600">{message.localKnowledge?.state === 'unavailable'
            ? 'Local evidence is unavailable. A source may have changed or been removed.'
            : 'This response is withheld because its source access cannot be verified.'}</p>
        ) : message.error ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 font-semibold text-sm text-rose-600">
              <AlertTriangle className="w-4 h-4" />
              <span>{message.textFileAnswer && message.plan ? 'File workflow incomplete' : 'Response failed'}</span>
            </div>
            <p role="alert" className="text-sm bg-rose-50 p-3 rounded-lg border border-rose-100 [overflow-wrap:anywhere]">
              {message.errorMessage || message.content}
            </p>
            {hasToolEvents && <ToolActivity events={message.toolEvents!} isStreaming={false} />}
            {message.textFileAnswer && plan}
            {message.errorMessage && message.content && (
              <div className="font-sans text-slate-800">
                <p className="text-xs font-medium text-slate-600">Partial response</p>
                {message.textFileAnswer ? <FileAnswer message={message} />
                  : <MarkdownContent content={message.content} />}
              </div>
            )}
            {onRetry && !message.textFileAnswer && (
              <button
                onClick={() => onRetry(message.content)}
                className="mt-3 px-4 py-2 rounded-lg bg-rose-500 hover:bg-rose-600 text-white font-medium text-sm transition-colors shadow-sm"
              >
                Retry
              </button>
            )}
          </div>
        ) : (
          <>
            {message.workflowStatus && <p role="status" className="mb-3 text-xs font-medium text-slate-600">
              {({ planning: 'Planning', executing: 'Executing', awaiting_approval: 'Waiting for approval',
                completed: 'Workflow completed', failed: 'Workflow failed', cancelled: 'Workflow cancelled; an operation already started may require review.',
                interrupted: 'Response interrupted; an operation already started may require review.' })[message.workflowStatus]}
            </p>}
            {/* Tool Activity Timeline */}
            {hasToolEvents && <ToolActivity events={message.toolEvents!} isStreaming={message.isStreaming} />}
            {message.imageProgress && !message.isStreaming && message.content && (
              <p className="mb-2 text-xs text-slate-600">AI-generated image interpretation</p>
            )}
            {plan}

            {/* Approval Card */}
            {!message.withheld && message.pendingApproval && onApprove && onReject && (
              <ApprovalCard
                taskId={message.pendingApproval.task_id}
                stepId={message.pendingApproval.step_id}
                approvalId={message.pendingApproval.approval_id}
                toolName={message.pendingApproval.tool_name}
                arguments={message.pendingApproval.arguments_hash ? (message.pendingApproval as any).arguments || {} : {}}
                riskLevel={message.pendingApproval.risk_level}
                reason={message.pendingApproval.reason}
                expiresAt={message.pendingApproval.expires_at}
                onApprove={onApprove}
                onReject={onReject}
              />
            )}

            {message.content ? (
              <div className="font-sans">
                {message.localKnowledge ? (
                  <>
                    <p className="mb-2 text-xs font-medium text-slate-600">Local knowledge</p>
                    <p className="whitespace-pre-wrap break-words text-sm">{message.content}</p>
                  </>
                ) : message.localVisual ? <p className="whitespace-pre-wrap break-words text-sm">{message.content}</p>
                  : message.textFileAnswer ? <FileAnswer message={message} />
                  : <MarkdownContent content={message.content} />}
              </div>
            ) : (
              message.isStreaming && (
                <div className="flex items-center gap-2 text-sm text-blue-600 py-2 font-medium">
                  <span className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" />
                  <span>
                    {message.imageProgress ? {
                      uploading: 'Uploading image…',
                      analyzing_image: 'Analyzing image…',
                      reasoning: 'Preparing image answer…',
                    }[message.imageProgress] : message.localAgentStatus === 'drafting' ? 'Assembling evidence draft.'
                      : message.localAgentStatus === 'analyzing_image' ? 'Analyzing image.'
                      : message.localAgentStatus === 'retrieving_evidence' ? 'Checking selected sources.'
                      : message.localAgentStatus === 'comparing_evidence' ? 'Comparing cited measurements.'
                      : message.localAgentStatus === 'planning' ? 'Planning…'
                      : message.localAgentStatus === 'tool_selected' && !hasToolEvents
                        ? (message.localSelectedTool === 'unit_conversion' ? 'Unit conversion selected' : 'Calculator selected')
                        : hasToolEvents ? 'Running tools...' : 'Thinking...'}
                  </span>
                </div>
              )
            )}

            {message.isStreaming && message.content && (
              <span className="inline-block w-2 h-4 ml-1 bg-blue-500 animate-pulse align-middle rounded-sm" />
            )}
          {!message.withheld && typeof message.numericSourceTurn === 'number' &&
            Number.isSafeInteger(message.numericSourceTurn) && message.numericSourceTurn > 0 &&
            <p className="mt-2 text-xs text-slate-600">
              {typeof message.numericSecondarySourceTurn === 'number' &&
                Number.isSafeInteger(message.numericSecondarySourceTurn) && message.numericSecondarySourceTurn > 0
                ? `Reused verified results from turns ${message.numericSourceTurn} and ${message.numericSecondarySourceTurn}.`
                : `Reused verified result from turn ${message.numericSourceTurn}.`}
            </p>}
          {message.localPreview && message.content && <p className="mt-2 text-xs text-slate-600">Streaming preview</p>}
          </>
        )}

        {!message.withheld && message.localKnowledge?.coverage && (
          <ul aria-label="Evidence by question part" className="mt-3 space-y-2 border-t border-slate-200 pt-2 text-xs">
            {message.localKnowledge.coverage.map((part, index) => (
              <li key={index}>
                <span className="font-medium">{({
                  supported: 'Cited evidence', incomplete: 'Incomplete evidence',
                  absent: 'No evidence', conflict: 'Conflicting evidence',
                })[part.state]}</span>
                {' · '}{part.question}
                {part.citations.map(number => (
                  <a key={number} className="ml-1 underline" href={`#evidence-${message.id}-${number}`}
                    onClick={() => {
                      const source = document.getElementById(`evidence-${message.id}-${number}`) as HTMLDetailsElement | null;
                      if (source) source.open = true;
                    }}>[{number}]</a>
                ))}
                {part.fields?.length ? (
                  <ul aria-label="Attributed source values" className="mt-1 space-y-1 text-slate-700">
                    {part.fields.map(field => (
                      <li key={`${field.citation}-${field.start}-${field.end}`}>
                        <strong>{field.value}{field.value.endsWith(field.unit) ? '' : ` ${field.unit}`}</strong>
                        {' · '}{field.header || field.attribute.replaceAll('_', ' ')}
                        {[...field.equipment, ...field.conditions].map(label => ` · ${label}`).join('')}
                        {' · '}
                        <a className="underline" href={`#evidence-${message.id}-${field.citation}`}
                          onClick={() => {
                            const source = document.getElementById(`evidence-${message.id}-${field.citation}`) as HTMLDetailsElement | null;
                            if (source) source.open = true;
                          }}>
                          {field.document_locator ? documentPosition(field.document_locator)
                            : `line ${field.line}${field.column !== undefined ? `, column ${field.column}` : ''}`}
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {!message.withheld && message.localKnowledge && message.localKnowledge.citations.length > 0 && (
          <section aria-label="Verified local citations" className="mt-3 space-y-2">
            {message.localKnowledge.citations.map(citation => (
              <details id={`evidence-${message.id}-${citation.number}`} key={citation.passage_id}
                className="rounded-lg border border-slate-200 p-2 text-xs">
                <summary className="cursor-pointer break-words font-medium">
                  [{citation.number}] {citation.label} · {citation.document_locator
                    ? documentPosition(citation.document_locator)
                    : citation.line_end && citation.line_end > citation.line
                      ? `lines ${citation.line}–${citation.line_end}` : `line ${citation.line}`} · revision {citation.revision}
                </summary>
                {citation.sections?.length ? <p className="mt-2 break-words font-medium">
                  {citation.sections.map(section => citation.document_locator ? section.title
                    : `${section.title} (line ${section.line})`).join(' / ')}
                </p> : citation.heading && <p className="mt-2 break-words font-medium">
                  Section {citation.heading} · line {citation.heading_line}
                </p>}
                {citation.equipment?.length ? <p className="mt-1">Equipment · {citation.equipment.join(', ')}</p> : null}
                {citation.measurements?.length && !message.localKnowledge?.coverage?.some(
                  part => part.fields?.some(field => field.citation === citation.number)
                ) ? <p className="mt-1">Source measurements · {citation.measurements.join(', ')}</p> : null}
                {citation.table_header && <p className="mt-1 break-words">Table columns · {citation.table_header}</p>}
                <p className="mt-2 whitespace-pre-wrap break-words">{citation.excerpt}</p>
                {citation.match_reasons?.length ? <p className="mt-2 text-slate-600">
                  {citation.match_reasons.map(reason => ({
                    equipment: 'Matching equipment identifier',
                    section: 'Matching section or table context',
                    exact_measurement: 'Matching source measurement',
                    equivalent_measurement: 'Equivalent measurement in a supported unit',
                    within_range: 'Requested value within source range',
                  })[reason]).join(' · ')}
                </p> : null}
              </details>
            ))}
          </section>
        )}

        {/* Sources Grid */}
        {!message.withheld && !message.textFileAnswer && message.sources && message.sources.length > 0 && (
          <div className="mt-6 pt-5 border-t border-slate-100">
            <div className="text-xs font-semibold text-slate-500 tracking-wide mb-3">
              Sources & Evidence
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {message.sources.map((src, idx) => (
                <SourceCard key={src.chunk_id || idx} source={src} />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Copy / Actions Footer */}
      {!message.withheld && !message.isStreaming && !message.error && message.content && (
        <div className="flex items-center justify-end gap-2 mt-1">
          <button
            onClick={handleCopy}
            className="px-3 py-1.5 rounded-lg bg-white border border-slate-200 text-slate-600 hover:text-blue-600 hover:border-blue-200 hover:bg-blue-50 font-medium text-xs flex items-center gap-1.5 shadow-sm transition-colors"
          >
            {copied ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-500" />
                <span className="text-emerald-600">Copied</span>
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" />
                <span>Copy</span>
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
};
