import { useState } from 'react';
import type { StatuteSource } from './StatuteTab';

/**
 * One retrieved provision, expandable to its full statutory text.
 *
 * The law badge and the repeal/currency warnings are rendered on the collapsed
 * card, not hidden behind the expander: a reader must not be able to skim a
 * citation without seeing that the provision is repealed or that its text
 * predates a later amendment.
 */
export function SourceCard({ source }: { source: StatuteSource }) {
  const [open, setOpen] = useState(false);

  const isRepealed = source.legal_status === 'repealed';
  const lawStyle = isRepealed
    ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
    : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30';

  return (
    <div className="bg-slate-950/60 border border-slate-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen((value) => !value)}
        className="w-full text-left px-3 py-2.5 hover:bg-slate-900/60 transition-colors"
      >
        <div className="flex items-start gap-2">
          <span className={`flex-shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded border ${lawStyle}`}>
            {source.law}
          </span>
          <span className="flex-shrink-0 text-xs font-mono text-slate-300">
            s.{source.section}
          </span>
          <span className="text-xs text-slate-300 flex-1 leading-snug">{source.title}</span>
          <span className="flex-shrink-0 text-[10px] text-slate-500 font-mono">
            {source.retrieval_score.toFixed(3)}
          </span>
          <span className="flex-shrink-0 text-slate-600 text-[10px]">{open ? '▾' : '▸'}</span>
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-500 pl-1">
          <span>{source.act_title}</span>
          {isRepealed && (
            <span className="text-amber-400/90">
              repealed 1 Jul 2024 → replaced by BNS
            </span>
          )}
          {source.exact_section_match && (
            <span className="text-indigo-400/90">exact section match</span>
          )}
        </div>

        {source.superseded_note && (
          <p className="mt-1.5 text-[10px] text-amber-200/90 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-1 leading-snug">
            ⚠ {source.superseded_note}
          </p>
        )}
      </button>

      {open && (
        <div className="px-3 pb-3 border-t border-slate-800/80 pt-2.5 flex flex-col gap-2">
          {source.chapter && (
            <p className="text-[10px] text-slate-500">
              {source.chapter}
              {source.chapter_title ? ` — ${source.chapter_title}` : ''}
            </p>
          )}

          <div className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap max-h-72 overflow-y-auto custom-scrollbar bg-slate-950 rounded-md p-2.5 border border-slate-800">
            {source.text || 'No statutory text stored for this section.'}
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-500">
            <span>
              Source: <span className="text-slate-400">{source.source}</span>
            </span>
            {source.amended_up_to && <span>Text as amended up to {source.amended_up_to}</span>}
            {source.url && (
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-400 hover:text-indigo-300 underline decoration-dotted"
              >
                View official PDF ↗
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
