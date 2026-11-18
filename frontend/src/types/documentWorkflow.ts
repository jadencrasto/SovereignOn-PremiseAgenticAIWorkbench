// JSON presentation from the authorized in-process document task. This is not a chat or SSE contract.
export type DocumentFormat = 'pdf' | 'docx' | 'xlsx';

export interface DocumentLocation {
  page?: number;
  paragraph?: number;
  body?: number;
  table?: number;
  row?: number;
  column?: number;
  sheet?: string;
  cell?: string;
}

interface DocumentSource {
  source_id: string;
  filename: string;
  format: DocumentFormat;
  warnings: { code: string; location: DocumentLocation }[];
}

interface Citation {
  source_id: string;
  location: DocumentLocation;
  value_type: 'text' | 'number' | 'boolean' | 'date_time' | 'duration' | 'cell_error';
  value: string;
  number_format?: string;
  heading_level?: number;
}

type Finding = { evidence: Citation[] } & (
  { kind: 'passage' } |
  { kind: 'calculation'; operation: string; value: string; unit: string | null; approximate: boolean;
    excluded: { missing: number; nonnumeric: number; formulas: number } } |
  { kind: 'comparison'; unit: string | null; rows: { record: string; difference: string; operands: [number, number] }[] }
);

export interface DocumentOutcome {
  text: string;
  sources: DocumentSource[];
  findings: Finding[];
}

interface PlannedStep { id: string; description: string; status: 'pending' }
type StepStatus = 'running' | 'completed' | 'failed';

export type DocumentWorkflowEvent = { task_id: string } & (
  { type: 'task_started'; status: 'planning' } |
  { type: 'plan_created'; plan: { objective: string; steps: PlannedStep[] } } |
  { type: 'plan_step'; step_id: string; description: string; status: StepStatus } |
  { type: 'task_completed'; answer: DocumentOutcome } |
  { type: 'task_failed' | 'task_cancelled'; error_code: string; error: string;
    step_id: string | null; operation: string | null; partial: DocumentOutcome | null }
);
