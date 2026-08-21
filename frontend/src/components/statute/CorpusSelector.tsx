import { useEffect, useState } from 'react';
import { apiClient } from '../../api/client';

export type CorpusChoice = 'auto' | 'IPC' | 'BNS' | 'both';

interface LawInfo {
  law: 'IPC' | 'BNS';
  name: string;
  sections: number;
  status: string;
  status_note: string;
  currency_warning: string;
}

const OPTIONS: { value: CorpusChoice; label: string; hint: string }[] = [
  { value: 'auto', label: 'Auto', hint: 'Infer from the question; search both if unclear' },
  { value: 'IPC', label: 'IPC', hint: 'Indian Penal Code, 1860 — repealed 1 Jul 2024' },
  { value: 'BNS', label: 'BNS', hint: 'Bharatiya Nyaya Sanhita, 2023 — in force' },
  { value: 'both', label: 'Both', hint: 'Search both statutes and label every result' },
];

/**
 * Which statute the query runs against.
 *
 * This is surfaced prominently rather than buried in settings because IPC and
 * BNS are different law: the IPC was repealed on 1 July 2024, and answering a
 * question from the wrong one is a substantive error. "Auto" never silently
 * picks a side — when a question names no statute it searches both and each
 * result is labelled.
 */
export function CorpusSelector({
  value,
  onChange,
}: {
  value: CorpusChoice;
  onChange: (choice: CorpusChoice) => void;
}) {
  const [laws, setLaws] = useState<LawInfo[]>([]);

  useEffect(() => {
    apiClient
      .get('/statute/corpus')
      .then(({ data }) => setLaws(data.laws ?? []))
      .catch(() => setLaws([]));
  }, []);

  const counts = Object.fromEntries(laws.map((law) => [law.law, law.sections]));

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex rounded-lg border border-slate-700 overflow-hidden">
        {OPTIONS.map((option) => (
          <button
            key={option.value}
            onClick={() => onChange(option.value)}
            title={option.hint}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              value === option.value
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-950 text-slate-400 hover:text-slate-200 hover:bg-slate-900'
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
      {laws.length > 0 && (
        <p className="text-[10px] text-slate-500">
          IPC {counts.IPC ?? 0} sections · BNS {counts.BNS ?? 0} sections
        </p>
      )}
    </div>
  );
}
