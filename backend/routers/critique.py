"""
critique.py
Router for Adversarial Argument Critique Mode.
Generates structured counter-queries using diverse legal
attack angles and evaluates argument vulnerability.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging
import json

from backend.services.rag_service import rag_service
import config

logger = logging.getLogger(__name__)
router = APIRouter()


# -----------------------------
# Request Schema
# -----------------------------

class CritiqueRequest(BaseModel):
    argument: str


# -----------------------------
# Counter Query Generator
# (Priority-1 Improvement Applied)
# -----------------------------

async def generate_counter_queries(argument: str):
    """
    Generates structured adversarial legal search queries
    using multiple legal attack angles.
    """

    prompt = f"""
You are a senior legal researcher.

Generate 6 adversarial legal search queries.

Each query MUST target a DIFFERENT legal attack angle:

1. Exceptions to the doctrine
2. Reasonable restrictions
3. National security limitations
4. Public interest limitations
5. Proportionality doctrine
6. Cases where courts rejected similar claims

Argument:
{argument}

Return ONLY JSON:

{{
 "queries": [
   "exceptions to doctrine query",
   "reasonable restrictions query",
   "national security limitation query",
   "public interest limitation query",
   "proportionality doctrine query",
   "rejected claims query"
 ]
}}
"""

    client = rag_service.query_engine.client

    response = await client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=config.GROQ_MODEL,
        temperature=0.2,
        response_format={"type": "json_object"}
    )

    data = json.loads(response.choices[0].message.content)

    return data.get("queries", [])


# -----------------------------
# Main Critique Route
# -----------------------------

@router.post("/score")
async def score_argument(request: CritiqueRequest):

    if not rag_service.initialized or not rag_service.query_engine.client:
        raise HTTPException(
            status_code=503,
            detail="Service not ready."
        )

    try:

        logger.info("Starting adversarial critique")

        # ----------------------------------
        # STEP 1 — Generate Counter Queries
        # ----------------------------------

        counter_queries = await generate_counter_queries(
            request.argument
        )

        if not counter_queries:
            raise Exception(
                "Failed to generate counter queries"
            )

        logger.info(
            f"Generated queries: {counter_queries}"
        )


        # ----------------------------------
        # STEP 2 — Retrieve Context
        # ----------------------------------

        all_chunks = []

        for q in counter_queries:

            res = await rag_service.query(q)

            chunks = res.get(
                "context_chunks",
                []
            )

            all_chunks.extend(chunks)


        # Remove duplicates
        unique_chunks = []

        seen_texts = set()

        for c in all_chunks:

            text = c.get("text", "")

            if text and text not in seen_texts:

                seen_texts.add(text)

                unique_chunks.append(text)


        logger.info(
            f"Retrieved {len(unique_chunks)} unique chunks"
        )


        # Increase diversity (Priority-5 improvement)
        context_str = "\n\n".join(
            unique_chunks[:12]
        )


        # ----------------------------------
        # STEP 3 — Adversarial Critique
        # ----------------------------------

        prompt = f"""
You are a senior constitutional lawyer acting as opposing counsel.

Your job is to ATTACK the argument using legal precedent.

You must:

1. Identify logical weaknesses
2. Cite contradicting precedents
3. Explain how courts may reject the claim
4. Suggest a legally safer formulation
5. Assign vulnerability score

Return ONLY JSON:

{{
   "strike_score": (1-100),

   "weaknesses": [
      "legal flaw 1",
      "legal flaw 2"
   ],

   "counter_cases": [
      "Case Name → key holding",
      "Case Name → key holding"
   ],

   "suggested_fix":
      "Rewrite the argument in a stronger legally defensible way."
}}

Argument:
{request.argument}

Retrieved Counter Context:
{context_str}
"""

        client = rag_service.query_engine.client

        response = await client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model=config.GROQ_MODEL,
            temperature=0.1,
            response_format={"type": "json_object"}
        )

        data = json.loads(
            response.choices[0].message.content
        )


        # ----------------------------------
        # STEP 4 — Add Risk Level
        # (Priority-6 improvement)
        # ----------------------------------

        score = data.get(
            "strike_score",
            50
        )

        if score > 75:

            data["risk_level"] = "HIGH"

        elif score > 40:

            data["risk_level"] = "MEDIUM"

        else:

            data["risk_level"] = "LOW"


        logger.info(
            "Critique completed successfully"
        )

        return data


    except Exception as e:

        logger.error(
            f"Critique error: {e}"
        )

        raise HTTPException(
            status_code=500,
            detail="Request failed."
        )