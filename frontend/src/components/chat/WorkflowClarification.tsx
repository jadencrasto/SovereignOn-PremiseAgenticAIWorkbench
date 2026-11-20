import { useState } from 'react';
import type { LocalClarification } from '../../types';

export function WorkflowClarification({ clarification, busy, resume, cancel }: {
  clarification: LocalClarification;
  busy: boolean;
  resume: (selected: string[]) => void;
  cancel: () => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const { count, options, question } = clarification;
  return <fieldset disabled={busy} className="mx-5 my-2 rounded border border-slate-200 px-3 py-2 text-sm">
    <legend className="px-1 font-medium">{question}</legend>
    {options.length < count && <p>There aren’t enough available choices. Cancel this task and start again with the required files.</p>}
    <div className="flex flex-wrap gap-3">
      {options.map(option => <label key={option.id} className="flex items-center gap-1">
        <input type="checkbox" checked={selected.includes(option.id)}
          disabled={!selected.includes(option.id) && selected.length >= count && count > 1}
          onChange={event => setSelected(previous => event.target.checked
            ? count === 1 ? [option.id] : [...previous, option.id]
            : previous.filter(value => value !== option.id))} />
        {option.label}
      </label>)}
    </div>
    <div className="mt-2 flex gap-3">
      <button type="button" disabled={selected.length !== count} className="font-medium underline"
        onClick={() => resume(selected)}>Continue workflow</button>
      <button type="button" className="underline" onClick={cancel}>Cancel workflow</button>
    </div>
  </fieldset>;
}
