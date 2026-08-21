"""
Module: legal_dictionary.py
50+ Latin maxims and legal jargon definitions.
Injects synonyms during embedding and adds tooltip markers to LLM outputs.
"""

import re
from typing import List, Dict, Optional

LATIN_MAXIMS = {
    "audi alteram partem": "The right to be heard; no one should be condemned unheard.",
    "res judicata": "A matter already judged cannot be litigated again.",
    "certiorari": "A writ to quash a lower court's decision for want of jurisdiction.",
    "mutatis mutandis": "With necessary changes having been made.",
    "ratio decidendi": "The binding reason for a court's decision.",
    "obiter dicta": "Passing remarks by a judge; not legally binding.",
    "locus standi": "The right to bring an action before a court.",
    "in personam": "A legal action directed against a specific person.",
    "habeas corpus": "A writ requiring a detained person to be brought before a court.",
    "mandamus": "A court order to a public authority to perform a mandatory duty.",
    "suo motu": "On the court's own motion, without a party's application.",
    "ex parte": "Proceedings with only one party present.",
    "prima facie": "At first sight; sufficient to establish a case unless disproved.",
    "inter alia": "Among other things.",
    "per incuriam": "A decision made in error, without considering relevant law.",
    "in rem": "A legal action against a thing or status, not a person.",
    "ultra vires": "Beyond the legal power or authority of an act or person.",
    "intra vires": "Within the legal power or authority.",
    "nemo judex in causa sua": "No person shall be a judge in their own cause.",
    "mens rea": "The guilty mind; criminal intent required for most offences.",
    "actus reus": "The guilty act; the physical element of a crime.",
    "sub silentio": "In silence; a decision made without being expressly stated.",
    "stare decisis": "To stand by decided cases; doctrine of precedent.",
    "dura lex sed lex": "The law is harsh, but it is the law.",
    "ex aequo et bono": "According to what is equitable and good.",
    "volenti non fit injuria": "To a willing person, no injury is done.",
    "pari passu": "On equal footing; at the same rate.",
    "sine qua non": "An indispensable condition or element.",
    "de novo": "Anew; a fresh start, as if the earlier proceeding never occurred.",
    "ejusdem generis": "Of the same kind; a rule for interpreting statutes.",
    "ab initio": "From the beginning.",
    "ad hoc": "For this specific purpose.",
    "amicus curiae": "A friend of the court; an impartial adviser.",
    "bona fide": "In good faith.",
    "caveat emptor": "Let the buyer beware.",
    "compos mentis": "Of sound mind.",
    "corpus delicti": "The body of the offense; the basic element of a crime.",
    "de facto": "In fact, whether by right or not.",
    "de jure": "By right; legally.",
    "ex post facto": "After the fact; retrospectively.",
    "force majeure": "Unforeseeable circumstances that prevent someone from fulfilling a contract.",
    "ignorantia juris non excusat": "Ignorance of the law is not an excuse.",
    "in camera": "In private; typically a hearing without the public.",
    "ipso facto": "By that very fact or act.",
    "jus cogens": "A peremptory norm of general international law.",
    "lis pendens": "A pending legal action.",
    "non compos mentis": "Not of sound mind.",
    "pro bono": "For the public good; without charge.",
    "quantum meruit": "As much as he/she has earned.",
    "quid pro quo": "Something for something."
}

# Synonyms appended to chunks before embedding to pull Latin into the 
# same semantic space as English questions.
SYNONYM_PAIRS = {
    "audi alteram partem": "right to be heard fair hearing",
    "res judicata": "matter already decided finality",
    "ratio decidendi": "binding reason for decision core holding",
    "obiter dicta": "passing remarks not binding",
    "locus standi": "right to bring legal action standing before court",
    "mens rea": "criminal intent guilty mind",
    "actus reus": "criminal act physical element",
    "ultra vires": "beyond legal authority power",
    "habeas corpus": "unlawful detention body release",
    "mandamus": "compel public duty order",
    "suo motu": "own motion initiative",
    "ex parte": "one sided absent party",
    "prima facie": "first glance face value",
    "per incuriam": "bad law ignorance error"
}

class LegalDictionary:
    def __init__(self):
        # Sort by length descending to match longest phrases first
        self.sorted_terms = sorted(LATIN_MAXIMS.keys(), key=len, reverse=True)
        # Create a combined regex pattern
        escaped_terms = [re.escape(term) for term in self.sorted_terms]
        self.pattern = re.compile(r'\b(' + '|'.join(escaped_terms) + r')\b', re.IGNORECASE)

    def annotate_answer(self, text: str) -> str:
        """
        Wrap recognized Latin phrases with [[TERM||definition]] markers.
        """
        if not text:
            return text

        def repler(match: re.Match) -> str:
            term = match.group(1)
            term_lower = term.lower()
            if term_lower in LATIN_MAXIMS:
                defn = LATIN_MAXIMS[term_lower]
                return f"[[{term}||{defn}]]"
            return term

        return self.pattern.sub(repler, text)

    def inject_synonyms(self, chunk_text: str) -> str:
        """
        Append English synonym to chunk text before embedding.
        E.g., "...audi alteram partem..." -> "...audi alteram partem [right to be heard fair hearing]"
        """
        if not chunk_text:
            return chunk_text

        text_lower = chunk_text.lower()
        active_synonyms = []
        for term, synonym in SYNONYM_PAIRS.items():
            if term in text_lower:
                active_synonyms.append(synonym)

        if active_synonyms:
            return chunk_text + "\n\n[Semantics: " + ", ".join(active_synonyms) + "]"
        
        return chunk_text

    def get_all_maxims(self) -> List[Dict[str, str]]:
        """Returns list of {term, definition} for the frontend glossary."""
        return [{"term": term, "definition": defn} for term, defn in LATIN_MAXIMS.items()]
