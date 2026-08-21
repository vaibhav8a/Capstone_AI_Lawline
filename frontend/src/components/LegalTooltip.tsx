import { useState, useRef } from 'react';

interface TooltipProps {
  term: string;
  definition: string;
  children: React.ReactNode;
}

/** Floating tooltip card shown on hover */
export function LegalTooltip({ term, definition, children }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const ref = useRef<HTMLSpanElement>(null);

  const handleMouseEnter = () => {
    if (ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setPos({
        top: rect.bottom + window.scrollY + 6,
        left: Math.min(rect.left + window.scrollX, window.innerWidth - 300),
      });
    }
    setVisible(true);
  };

  return (
    <>
      <span
        ref={ref}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={() => setVisible(false)}
        onFocus={handleMouseEnter}
        onBlur={() => setVisible(false)}
        tabIndex={0}
        className="
          italic text-indigo-300 underline decoration-dotted decoration-indigo-400/50 
          cursor-help transition-colors hover:text-indigo-200 hover:decoration-indigo-300
          focus:outline-none focus:ring-1 focus:ring-indigo-500 rounded-sm
        "
      >
        {children}
      </span>
      {visible && (
        <span
          style={{ top: pos.top, left: pos.left }}
          className="
            fixed z-[9999] w-72 bg-slate-800 border border-indigo-500/40 
            rounded-xl shadow-2xl shadow-black/50 p-4 pointer-events-none
            animate-in fade-in duration-150
          "
          role="tooltip"
        >
          <span className="block text-xs font-bold text-indigo-400 uppercase tracking-widest mb-1.5">
            {term}
          </span>
          <span className="block text-sm text-slate-200 leading-relaxed">
            {definition}
          </span>
          <span className="block mt-2 text-xs text-slate-500 italic">
            Legal Latin Maxim
          </span>
        </span>
      )}
    </>
  );
}

/**
 * Regex that matches [[term||definition]] markers injected by the backend.
 * Splits a text node into plain text + tooltip segments.
 */
const MARKER_RE = /\[\[([^\|]+)\|\|([^\]]+)\]\]/g;

export interface TextSegment {
  type: 'plain' | 'tooltip';
  text: string;
  definition?: string;
}

export function parseTooltipMarkers(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  MARKER_RE.lastIndex = 0;
  while ((match = MARKER_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'plain', text: text.slice(lastIndex, match.index) });
    }
    segments.push({ type: 'tooltip', text: match[1], definition: match[2] });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push({ type: 'plain', text: text.slice(lastIndex) });
  }
  return segments;
}

/**
 * Renders a string that may contain [[term||def]] markers,
 * converting them into LegalTooltip components.
 */
export function TooltipText({ text }: { text: string }) {
  const segments = parseTooltipMarkers(text);
  return (
    <>
      {segments.map((seg, i) =>
        seg.type === 'tooltip' ? (
          <LegalTooltip key={i} term={seg.text} definition={seg.definition!}>
            {seg.text}
          </LegalTooltip>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </>
  );
}
