"""
Module: case_summarizer.py
Structured legal cheat-sheet generator.

Outputs:
Facts / Issues / Law Applied / Ratio / Holding / Significance
"""

import json
from typing import Dict, Any

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

import config


# =========================================
# FIXED PROMPT (UI-Compatible Schema)
# =========================================

PROMPT_TEMPLATE = """You are an expert legal researcher.

Summarize the following case into a structured legal cheat-sheet.

Return ONLY JSON with EXACT fields:

{
    "case_title": "Name of the case",

    "facts": ["Key fact 1", "Key fact 2"],

    "issues": ["Legal issue 1", "Legal issue 2"],

    "ratio_decidendi": "Core legal reasoning of the court (binding reason for the decision)",

    "holding": "Final decision of the case",

    "obiter_dicta": "Remarks by the judge that are not legally binding but provide context"
}

Case Text:
{document_text}
"""


# =========================================
# CASE SUMMARIZER
# =========================================

class CaseSummarizer:

    def __init__(self):
        pass


    async def summarize_case_async(
        self,
        document_text: str
    ) -> Dict[str, Any]:

        """
        Generates structured legal cheat-sheet.
        Always returns usable fields.
        """

        import logging
        logger = logging.getLogger(__name__)

        from groq import AsyncGroq

        api_key = config.GROQ_API_KEY

        if not api_key:

            return self._fallback_summary(
                "Missing GROQ_API_KEY"
            )

        client = AsyncGroq(api_key=api_key)

        prompt = PROMPT_TEMPLATE.replace(
            "{document_text}",
            document_text[:15000]
        )

        try:

            response = await client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content":
                        "Return ONLY valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model=config.GROQ_MODEL,
                temperature=0.1,
                response_format={
                    "type": "json_object"
                }
            )

            raw = response.choices[0].message.content

            data = json.loads(raw)

            logger.info(
                "[CaseSummarizer] Summary generated"
            )

            return data

        except Exception as e:

            logger.error(
                f"[CaseSummarizer] Failed: {e}"
            )

            return self._fallback_summary(
                str(e)
            )


    # =========================================
    # FALLBACK (Prevents Blank UI)
    # =========================================

    def _fallback_summary(
        self,
        error_msg: str
    ) -> Dict[str, Any]:

        return {

            "case_title": "Summary Unavailable",

            "facts": [
                "Unable to extract facts automatically."
            ],

            "issues": [
                "Summary generation encountered an error."
            ],

            "ratio_decidendi":
                "Could not determine the binding legal reasoning.",

            "holding":
                "Decision could not be extracted.",

            "obiter_dicta":
                f"Summary generation failed: {error_msg}"
        }


    # =========================================
    # Sync Wrapper
    # =========================================

    def summarize_case(
        self,
        document_text: str
    ) -> Dict[str, Any]:

        import asyncio

        return asyncio.run(
            self.summarize_case_async(
                document_text
            )
        )