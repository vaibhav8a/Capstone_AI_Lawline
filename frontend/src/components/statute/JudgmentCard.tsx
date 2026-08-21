import { useState } from 'react';

export interface JudgmentSource {
  source_type: 'judgment';
  law: string;
  case_name: string;
  court: string;
  judgment_date: string;
  citation: string;
  neutral_citation: string;
  judge: string;
  sections_referred: string[];
  text: string;
  source: string;
  url: string;
  document_id: string;
  retrieval_score: number;
  case_score: number;
  why_relevant: string[];
}

/**
 * One Supreme Court judgment, with its citation and the reason it was included.
 *
 * The relevance reason is rendered on the collapsed card rather than hidden
 * behind the expander. A list of case names with no stated reason is exactly the
 * "ten unrelated judgments that happen to mention a number" failure the ranking
 * work exists to avoid — if the system cannot say WHY a case is here, the user
 * has no way to judge whether it belongs.
 *
 * Every field shown comes from the judgment's own metadata. Nothing is inferred:
 * where a field is absent it is omitted rather than filled in.
 */
export function JudgmentCard({ judgment, rank }: { judgment: JudgmentSource; rank: number }) {
  const [open, setOpen] = useState(false);

  // Bands are presentational only — they describe retrieval score, not legal
  // authority, and are labelled that way to avoid implying precedential weight.
  const strength =
    judgment.case_score >= 1.0 ? 'strong' : judgment.case_score >= 0.7 ? 'moderate' : 'weak';
  const strengthStyle =
    strength === 'strong'
      ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
      : strength === 'moderate'
      ? 'bg-sky-500/15 text-sky-300 border-sky-500/30'
      : 'bg-slate-500/15 text-slate-400 border-slate-500/30';

  return (
    <div className="bg-slate-950/60 border border-slate-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((value) => !value)}
        className="w-full text-left px-3 py-2.5 hover:bg-slate-900/60 transition-colors"
      >
        <div className="flex items-start gap-2">
          <span className="flex-shrink-0 text-[10px] font-mono text-slate-500 mt-0.5">
            {rank}.
          </span>
          <span className="text-xs text-slate-200 font-medium flex-1 leading-snug">
            {judgment.case_name}
          </span>
          <span
            className={`flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${strengthStyle}`}
            title="Retrieval match strength — not a measure of precedential authority"
          >
            {strength}
          </span>
          <span className="flex-shrink-0 text-slate-600 text-[10px]">{open ? '▾' : '▸'}</span>
        </div>

        <div className="mt-1 pl-5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-slate-500">
          <span>{judgment.court}</span>
          {judgment.judgment_date && <span>{judgment.judgment_date}</span>}
          {judgment.citation && <span className="font-mono">{judgment.citation}</span>}
          {judgment.neutral_citation && (
            <span className="font-mono text-slate-600">{judgment.neutral_citation}</span>
          )}
        </div>

        {judgment.why_relevant?.length > 0 && (
          <p className="mt-1.5 pl-5 text-[11px] text-indigo-300/90 leading-snug">
            <span className="text-slate-500">Why relevant: </span>
            {judgment.why_relevant.join('; ')}
          </p>
        )}
      </button>

      {open && (
        <div className="px-3 pb-3 pt-2.5 border-t border-slate-800/80 flex flex-col gap-2">
          {judgment.judge && (
            <p className="text-[10px] text-slate-500">
              <span className="text-slate-600">Bench: </span>
              {judgment.judge}
            </p>
          )}

          {judgment.sections_referred?.length > 0 && (
            <p className="text-[10px] text-slate-500 leading-relaxed">
              <span className="text-slate-600">Sections cited: </span>
              {judgment.sections_referred.slice(0, 14).join(', ')}
              {judgment.sections_referred.length > 14 && ' …'}
            </p>
          )}

          <div>
            <p className="text-[10px] text-slate-600 mb-1">Supporting passage (as retrieved)</p>
            <div className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap max-h-64 overflow-y-auto custom-scrollbar bg-slate-950 rounded-md p-2.5 border border-slate-800">
              {judgment.text || 'No passage text stored for this result.'}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-500">
            <span>
              Source: <span className="text-slate-400">{judgment.source}</span>
            </span>
            <span className="font-mono">match {judgment.retrieval_score.toFixed(3)}</span>
            {judgment.url && (
              <a
                href={judgment.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-400 hover:text-indigo-300 underline decoration-dotted"
                onClick={(event) => event.stopPropagation()}
              >
                View judgment PDF ↗
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
