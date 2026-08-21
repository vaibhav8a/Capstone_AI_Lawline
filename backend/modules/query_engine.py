"""
Module: query_engine.py
LLM execution with HyDE (Hypothetical Document Embeddings), Query Decomposition, 
and Self-RAG Critic for hallucination mitigation.
"""

import json
import logging
from typing import AsyncGenerator, Dict, Any, List

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

class QueryEngine:
    def __init__(self):
        from groq import AsyncGroq
        api_key = config.GROQ_API_KEY
        self.client = AsyncGroq(api_key=api_key) if api_key else None
        self._active_model = config.GROQ_MODEL

    async def _chat_create(self, **kwargs):
        """
        Groq chat wrapper with decommission fallback.
        """
        models = [self._active_model] + [m for m in config.GROQ_FALLBACK_MODELS if m != self._active_model]
        last_error = None
        for model in models:
            try:
                res = await self.client.chat.completions.create(model=model, **kwargs)
                self._active_model = model
                return res
            except Exception as e:
                last_error = e
                msg = str(e).lower()
                if "decommission" in msg or "model_decommissioned" in msg or "no longer supported" in msg:
                    logger.warning(f"[QueryEngine] Groq model '{model}' unavailable, trying fallback.")
                    continue
                raise
        raise last_error

    async def decompose_query_llm(self, query: str) -> List[str]:
        """
        Uses LLM to split a complex query into atomic lookups.
        """
        if not config.QUERY_DECOMPOSE_ENABLED or not self.client:
            return [query]
            
        prompt = f"""Break this legal query into independent search lookups.
Return valid JSON. Preferred format:
{{"queries": ["search 1", "search 2"]}}
Query: {query}"""

        try:
            res = await self._chat_create(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            raw = res.choices[0].message.content or ""
            data = json.loads(raw)
            if isinstance(data, dict):
                for val in data.values():
                    if isinstance(val, list) and val:
                        return [str(v).strip() for v in val if str(v).strip()]
            if isinstance(data, list) and data:
                return [str(v).strip() for v in data if str(v).strip()]
            return [query]
        except Exception as e:
            logger.warning(f"[QueryEngine] Decomposition failed: {e}")
            return [query]

    async def generate_hyde_document(self, query: str) -> str:
        """
        Generates a hypothetical document to better match vector space.
        """
        if not config.HYDE_ENABLED or not self.client:
            return query
            
        prompt = f"""Please write a passage from a fictional Supreme Court judgment that directly answers this query:
{query}
Keep it under 150 words and use formal legal terminology."""

        try:
            res = await self._chat_create(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5
            )
            hyde_doc = res.choices[0].message.content.strip()
            # Combine original query with HyDE doc
            return f"{query}\n\n[HyDE Context]: {hyde_doc}"
        except Exception as e:
            logger.warning(f"[QueryEngine] HyDE failed: {e}")
            return query

    async def self_rag_critic(self, query: str, context: str, response: str) -> Dict[str, Any]:
        """
        Adversarial evaluation of the response against the context.
        Returns a critique object with a boolean 'safe' flag.
        """
        if not config.SELF_RAG_ENABLED or not self.client:
            return {"safe": True, "critique": "Self-RAG Disabled"}
            
        prompt = f"""You are a harsh legal critic. Review the generated response against the provided context.
Does the response:
1. Hallucinate facts NOT in the context?
2. Misrepresent the law?

Score it:
{{
  "hallucinated": boolean,
  "safe": boolean,
  "reasoning": "brief explanation"
}}
Return ONLY valid JSON.

Query: {query}
Context: {context}
Response: {response}"""

        try:
            res = await self._chat_create(
                messages=[
                    {"role": "system", "content": "You are a legal safety validator."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            raw = res.choices[0].message.content
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"[QueryEngine] Critic failed: {e}")
            return {"safe": True, "critique": str(e)}

    async def execute_streaming(
        self,
        query: str,
        context_chunks: List[Dict],
        use_self_rag: bool = True,
        chat_history: List[Dict[str, str]] | None = None
    ) -> AsyncGenerator[str, None]:
        if not self.client:
            # Fast extractive fallback so the product remains usable without GROQ key.
            yield self._build_fallback_answer(query, context_chunks)
            return
            
        context_str = "\n\n---\n\n".join(
            [f"[Case: {c.get('case_title')}]\n{c.get('text', c.get('parent_text', ''))}" for c in context_chunks]
        )
        
        system_prompt = "You are an expert Supreme Court researcher. Answer the query definitively using ONLY the provided context. Use markdown formatting. Cite the cases used. Use Indian legal terminology."
        history = chat_history or []
        history_block = ""
        if history:
            compact_turns = history[-6:]
            lines = []
            for turn in compact_turns:
                role = "User" if turn.get("role") == "user" else "Assistant"
                content = (turn.get("content") or "").strip()
                if content:
                    lines.append(f"{role}: {content}")
            if lines:
                history_block = "\n\nConversation so far:\n" + "\n".join(lines)
        
        try:
            stream = await self._chat_create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Context:\n{context_str}{history_block}\n\nCurrent Query: {query}"}
                ],
                temperature=0.3,
                stream=True
            )
            
            full_response = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full_response += text
                    # Stream raw tokens immediately for responsiveness
                    yield text
                    
            # After generation: annotate full response with [[term||definition]] markers
            # Send a special SSE marker so the frontend knows to replace the raw text
            from backend.modules.legal_dictionary import LegalDictionary
            dictionary = LegalDictionary()
            annotated = dictionary.annotate_answer(full_response)
            if annotated != full_response:
                # Only send replacement if there were actually terms found
                import json
                yield f"\n\n__ANNOTATED__:{json.dumps(annotated)}"
                    
            # After generation, run Critic if enabled
            if config.SELF_RAG_ENABLED and use_self_rag:
                critique = await self.self_rag_critic(query, context_str, full_response)
                if not critique.get("safe", True):
                    warning = f"\n\n> **[Self-RAG Warning]** The critic determined this response may contain unverified statements:\n> {critique.get('reasoning')}"
                    yield warning

        except Exception as e:
            logger.error(f"[QueryEngine] Stream generation failed: {e}")
            yield f"\n\nError generating response: str({e})"

    def _build_fallback_answer(self, query: str, context_chunks: List[Dict]) -> str:
        if not context_chunks:
            return (
                "No language model is configured, and no retrieval context was found for this query. "
                "Please configure `GROQ_API_KEY` in `.env` for generative answers."
            )

        top_chunks = context_chunks[:4]
        lines = [
            "### Fast Answer (Context-Only Mode)",
            f"Query: {query}",
            "",
            "Below is an extractive answer synthesized from retrieved authorities.",
            "",
            "#### Key Points",
        ]
        for idx, c in enumerate(top_chunks, start=1):
            case = c.get("case_title", "Unknown Case")
            court = c.get("court", "Unknown Court")
            date = c.get("date", "")
            text = (c.get("text") or c.get("parent_text") or "").strip().replace("\n", " ")
            snippet = text[:380] + ("..." if len(text) > 380 else "")
            lines.append(f"{idx}. **{case}** ({court}{f', {date}' if date else ''})")
            lines.append(f"   - {snippet}")

        lines.extend(
            [
                "",
                "#### Sources",
            ]
        )
        for c in top_chunks:
            lines.append(f"- {c.get('case_title', 'Unknown Case')} — chunk `{c.get('chunk_id', 'n/a')}`")
        lines.append("")
        lines.append(
            "_Tip: set `GROQ_API_KEY` to enable full LLM generation, HyDE, and Self-RAG critic output._"
        )
        return "\n".join(lines)
