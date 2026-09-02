"""
services/llm.py — Provider-agnostic LLM interface.

Two roles:
  RUNTIME — answers user queries. Uses Groq (free, fast).
  JUDGE   — scores RAGAS evaluation runs (Phase 2+). Uses OpenAI gpt-4o-mini.
             The judge runs infrequently (once per eval run), so the cost
             is a few cents — acceptable for reliable, reproducible scores.
             Small free models (Groq/Llama) produce noisy RAGAS scores that
             don't hold up under scrutiny.

Swapping providers is a .env change, not a code change.
"""
import os
from enum import Enum

from dotenv import load_dotenv

load_dotenv()

# System prompt: locked in from Phase 1 so Phase 3's agentic loop builds on
# a model that already refuses to guess beyond its context window.
INVESTIGATOR_SYSTEM_PROMPT = """You are an incident investigation assistant for an engineering team.

Rules you must follow:
1. Answer ONLY using the context provided below (retrieved postmortems and/or logs). \
Never use outside knowledge about real companies, real incidents, or general assumptions.
2. Every factual claim you make must cite the incident it came from, using the \
format [incident: <incident_id>].
3. If the provided context does not contain enough information to identify a \
likely root cause, say so explicitly: state "Insufficient evidence" and list what \
additional information (e.g. logs for a specific service/time window, or a related \
postmortem) would help. Do not guess.
4. Be concise. Prefer a short, direct answer with citations over a long narrative.
"""


class Role(str, Enum):
    RUNTIME = "runtime"  # answering user queries — cheap/free model
    JUDGE = "judge"       # RAGAS evaluation only — paid, higher-fidelity model


_ROLE_CONFIG = {
    Role.RUNTIME: {
        "provider": os.getenv("RUNTIME_LLM_PROVIDER", "groq"),
        "model": os.getenv("RUNTIME_LLM_MODEL", "llama-3.1-8b-instant"),
    },
    Role.JUDGE: {
        "provider": os.getenv("JUDGE_LLM_PROVIDER", "openai"),
        "model": os.getenv("JUDGE_LLM_MODEL", "gpt-4o-mini"),
    },
}


def generate(prompt: str, role: Role = Role.RUNTIME, system: str | None = None) -> str:
    """
    Single entry point for all LLM calls in the project.
    Dispatches to the configured provider for the given role.
    """
    config = _ROLE_CONFIG[role]
    provider = config["provider"]

    if provider == "groq":
        return _call_groq(prompt, config["model"], system)
    elif provider == "gemini":
        return _call_gemini(prompt, config["model"], system)
    elif provider == "openai":
        return _call_openai(prompt, config["model"], system)
    else:
        raise ValueError(f"Unknown LLM provider: '{provider}'. Expected: groq | gemini | openai")


def build_query_prompt(user_query: str, retrieved_chunks: list[dict]) -> str:
    """
    Build the user-turn prompt for a /query call.
    retrieved_chunks: list of {"incident_id": str, "chunk_text": str}
    """
    context_block = "\n\n".join(
        f"[incident: {c['incident_id']}]\n{c['chunk_text']}" for c in retrieved_chunks
    )
    return f"""Context (retrieved postmortem excerpts):
{context_block}

User's incident description:
{user_query}

Respond following the rules in your system prompt."""


# ---------------------------------------------------------------------------
# Provider implementations — imported lazily so unused providers don't need
# their SDK installed.
# ---------------------------------------------------------------------------

def _call_groq(prompt: str, model: str, system: str | None) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    resp = client.chat.completions.create(model=model, messages=messages)
    return resp.choices[0].message.content


def _call_gemini(prompt: str, model: str, system: str | None) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    gmodel = genai.GenerativeModel(model, system_instruction=system)
    resp = gmodel.generate_content(prompt)
    return resp.text


def _call_openai(prompt: str, model: str, system: str | None) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    resp = client.chat.completions.create(model=model, messages=messages)
    return resp.choices[0].message.content
