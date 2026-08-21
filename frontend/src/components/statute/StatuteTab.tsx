import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { apiClient } from '../../api/client';
import { SourceCard } from './SourceCard';
import { CorpusSelector, type CorpusChoice } from './CorpusSelector';
import { JudgmentCard, type JudgmentSource } from './JudgmentCard';

export interface StatuteSource {
  law: 'IPC' | 'BNS';
  section: string;
  title: string;
  chapter: string;
  chapter_title: string;
  text: string;
  source: string;
  url: string;
  act_title: string;
  legal_status: string;
  amended_up_to: string;
  superseded_note: string;
  retrieval_score: number;
  exact_section_match?: boolean;
}

interface StatuteAnswer {
  answer: string;
  sources: StatuteSource[];
  statutes?: StatuteSource[];
  judgments?: JudgmentSource[];
  routing?: { query_type: string; reason: string };
  premise_problems?: string[];
  abstained: boolean;
  llm_used: boolean;
  disclaimer: string;
  corpus_disclosure: string;
  corpus: { law: string | null; ambiguous: boolean; reason: string; section: string | null };
  abstention: { confidence: string; reasons: string[] };
  timings_ms: Record<string, number>;
  note?: string;
}

interface Turn {
  question: string;
  result?: StatuteAnswer;
  error?: string;
  pending: boolean;
}

// Examples are phrased as questions ABOUT provisions, never as assertions about
// what the law says — the UI must not make a legal claim the corpus has not been
// asked to support.
const EXAMPLES = [
  'What does IPC Section 420 deal with?',
  'What is the punishment mentioned under IPC Section 302?',
  'Which section deals with theft?',
  'What is the BNS provision for murder?',
];

export function StatuteTab() {
  const [question, setQuestion] = useState('');
  const [corpus, setCorpus] = useState<CorpusChoice>('auto');
  const [turns, setTurns] = useState<Turn[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns]);

  const ask = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    const index = turns.length;
    setTurns((prev) => [...prev, { question: trimmed, pending: true }]);
    setQuestion('');

    try {
      const { data } = await apiClient.post<StatuteAnswer>('/statute/legal-answer', {
        query: trimmed,
        ...(corpus === 'auto' ? {} : { corpus }),
      });
      setTurns((prev) =>
        prev.map((turn, i) => (i === index ? { ...turn, result: data, pending: false } : turn))
      );
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail ??
        (err?.message === 'Network Error'
          ? 'Could not reach the backend. Is it running on port 8000?'
          : 'Something went wrong.');
      const message = Array.isArray(detail)
        ? detail.map((d: any) => d.msg ?? String(d)).join('; ')
        : String(detail);
      setTurns((prev) =>
        prev.map((turn, i) => (i === index ? { ...turn, error: message, pending: false } : turn))
      );
    }
  };

  return (
    <div className="flex flex-col h-full max-w-4xl mx-auto w-full gap-4">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <header className="flex-shrink-0 bg-slate-900 border border-slate-700 rounded-xl p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-100">Indian Criminal Statute Assistant</h1>
            <p className="text-xs text-slate-400 mt-0.5">
              Grounded search over the Indian Penal Code, 1860 and the Bharatiya Nyaya Sanhita, 2023
            </p>
          </div>
          <CorpusSelector value={corpus} onChange={setCorpus} />
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-amber-200/80 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
          Legal information generated from public statutory text — not legal advice, and not a
          substitute for a qualified advocate. The IPC was repealed on 1 July 2024; the copy indexed
          here reflects amendments only up to 1997.
        </p>
      </header>

      {/* ── Conversation ───────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto custom-scrollbar flex flex-col gap-4 pr-1">
        {turns.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-5 text-center px-6">
            <div className="text-4xl">⚖️</div>
            <p className="text-slate-400 text-sm max-w-md">
              Ask about an offence or a section. Every answer cites the provisions it was built
              from, and says so when the corpus cannot support one.
            </p>
            <div className="flex flex-col gap-2 w-full max-w-md">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  onClick={() => ask(example)}
                  className="text-left text-sm px-4 py-2.5 rounded-lg bg-slate-900 border border-slate-700 text-slate-300 hover:border-indigo-500 hover:text-slate-100 transition-colors"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, index) => (
          <article key={index} className="flex flex-col gap-3">
            <div className="self-end max-w-[85%] bg-indigo-600/90 text-white rounded-2xl rounded-br-sm px-4 py-2.5 text-sm">
              {turn.question}
            </div>

            {turn.pending && (
              <div className="self-start flex items-center gap-2 text-slate-400 text-sm px-4 py-3">
                <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />
                <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse [animation-delay:150ms]" />
                <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse [animation-delay:300ms]" />
                <span className="ml-1">Searching the statute corpus…</span>
              </div>
            )}

            {turn.error && (
              <div className="self-start w-full bg-red-950/40 border border-red-500/40 rounded-xl px-4 py-3 text-sm text-red-200">
                <strong className="font-semibold">Request failed. </strong>
                {turn.error}
              </div>
            )}

            {turn.result && <AnswerBlock result={turn.result} />}
          </article>
        ))}
        <div ref={endRef} />
      </div>

      {/* ── Composer ───────────────────────────────────────────────────── */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          ask(question);
        }}
        className="flex-shrink-0 bg-slate-900 border border-slate-700 rounded-xl p-3 flex flex-col gap-2"
      >
        <textarea
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              ask(question);
            }
          }}
          rows={2}
          maxLength={2000}
          placeholder="Ask about an offence or a section — e.g. “Which section covers criminal breach of trust?”"
          className="w-full bg-slate-950 border border-slate-800 rounded-lg p-3 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500 resize-none"
        />
        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            {turns.length > 0 && (
              <button
                type="button"
                onClick={() => setTurns([])}
                className="text-xs px-3 py-1.5 rounded-md border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
              >
                Clear chat
              </button>
            )}
          </div>
          <button
            type="submit"
            disabled={!question.trim()}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white px-5 py-1.5 rounded-lg text-sm font-medium transition-colors"
          >
            Ask
          </button>
        </div>
      </form>
    </div>
  );
}

function AnswerBlock({ result }: { result: StatuteAnswer }) {
  const [copied, setCopied] = useState(false);
  const [showSources, setShowSources] = useState(true);
  const [showJudgments, setShowJudgments] = useState(true);

  const copy = async () => {
    await navigator.clipboard.writeText(result.answer);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const statutes = result.statutes ?? result.sources ?? [];
  const judgments = result.judgments ?? [];

  const confidence = result.abstention?.confidence ?? 'unknown';
  const confidenceStyle =
    confidence === 'high'
      ? 'text-emerald-300 border-emerald-500/30 bg-emerald-500/10'
      : confidence === 'medium'
      ? 'text-sky-300 border-sky-500/30 bg-sky-500/10'
      : 'text-amber-300 border-amber-500/30 bg-amber-500/10';

  return (
    <div className="self-start w-full bg-slate-900 border border-slate-700 rounded-xl overflow-hidden">
      {/* which corpus was consulted — always visible */}
      <div className="px-4 py-2 bg-slate-950/60 border-b border-slate-800 flex flex-wrap items-center gap-2 text-[11px]">
        <span className="text-slate-400">{result.corpus_disclosure}</span>
        {!result.abstained && (
          <span className={`ml-auto px-2 py-0.5 rounded-full border ${confidenceStyle}`}>
            retrieval confidence: {confidence}
          </span>
        )}
      </div>

      {result.abstained ? (
        <div className="px-4 py-4">
          <p className="text-sm text-amber-200">{result.answer}</p>
          {result.abstention?.reasons?.length > 0 && (
            <ul className="mt-2 text-[11px] text-slate-500 list-disc list-inside space-y-0.5">
              {result.abstention.reasons.map((reason, i) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="px-4 py-3">
          {!result.llm_used && result.note && (
            <p className="mb-3 text-[11px] text-slate-400 bg-slate-800/60 border border-slate-700 rounded-md px-3 py-2">
              {result.note}
            </p>
          )}
          <div className="prose prose-invert prose-sm prose-indigo max-w-none prose-headings:text-slate-200 prose-p:text-slate-300">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown>
          </div>
        </div>
      )}

      {statutes.length > 0 && (
        <div className="border-t border-slate-800">
          <button
            onClick={() => setShowSources((value) => !value)}
            className="w-full flex items-center justify-between px-4 py-2 text-xs text-slate-400 hover:text-slate-200 transition-colors"
          >
            <span>
              {showSources ? '▾' : '▸'} Statutory sources ({statutes.length})
            </span>
            <span className="text-[10px] text-slate-600">
              retrieval {result.timings_ms?.total_retrieval?.toFixed(0)} ms
            </span>
          </button>
          {showSources && (
            <div className="px-3 pb-3 flex flex-col gap-2">
              {statutes.map((source) => (
                <SourceCard key={`${source.law}-${source.section}`} source={source} />
              ))}
            </div>
          )}
        </div>
      )}


      {judgments.length > 0 && (
        <div className="border-t border-slate-800">
          <button
            onClick={() => setShowJudgments((value) => !value)}
            className="w-full flex items-center justify-between px-4 py-2 text-xs text-slate-400 hover:text-slate-200 transition-colors"
          >
            <span>
              {showJudgments ? '▾' : '▸'} Related Supreme Court judgments ({judgments.length})
            </span>
            <span className="text-[10px] text-slate-600">ranked by relevance to this question</span>
          </button>
          {showJudgments && (
            <div className="px-3 pb-3 flex flex-col gap-2">
              {judgments.map((judgment, i) => (
                <JudgmentCard
                  key={judgment.document_id ?? `${judgment.case_name}-${i}`}
                  judgment={judgment}
                  rank={i + 1}
                />
              ))}
              <p className="text-[10px] text-slate-600 leading-snug px-1">
                Judgments are retrieved from a 260-case Supreme Court corpus (1973–2023) and
                capped at five. Ranking reflects retrieval match and citation overlap, not
                precedential authority.
              </p>
            </div>
          )}
        </div>
      )}

      <div className="px-4 py-2 border-t border-slate-800 flex items-center justify-between gap-3">
        <p className="text-[10px] text-slate-500 leading-snug">{result.disclaimer}</p>
        <button
          onClick={copy}
          className="flex-shrink-0 text-[11px] px-2.5 py-1 rounded-md border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
        >
          {copied ? 'Copied' : 'Copy answer'}
        </button>
      </div>
    </div>
  );
}
