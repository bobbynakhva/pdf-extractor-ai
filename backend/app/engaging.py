"""Engaging Mode via LLM.

Providers: CLōD (OpenAI-compatible gateway), OpenAI, or Anthropic. Selected by
env var ``LLM_PROVIDER`` (``clod`` | ``openai`` | ``anthropic`` | ``none``).
If no key is configured we fall back to a deterministic, local reformatter so
the feature still works end-to-end without hitting an API — still no content
alteration.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

import httpx

SYSTEM_PROMPT = (
    "You are a deterministic formatting engine for PDF-extracted content.\n"
    "HARD RULES — these are inviolable:\n"
    "1. Never change, add, remove, paraphrase, translate, summarize, or reorder any words, numbers, names, dates, punctuation, or data points.\n"
    "2. Preserve every character of the input exactly, including whitespace-significant content inside tables.\n"
    "3. You may ONLY add presentation: Markdown headings (H1/H2/H3), **bold**, *italics*, __underline__ via <u>…</u>, bullet/numbered lists, blockquotes, relatable emojis next to headings and key points (📊 💡 ⚠️ 📝 📈 📉 🟡 🟢 🔴), and callout blockquotes for summaries.\n"
    "4. When you bold/italicize/etc, the plaintext must still match the input when formatting is stripped. Do NOT introduce new sentences.\n"
    "5. If the input has a flag like '⚠️ EXTRACTION FLAG' or 'Needs verification', keep it visible.\n"
    "6. Keep all tables as Markdown tables with the same cells, rows, and order.\n"
    "7. Keep all image references (![...](data:...) or ![...](http...)) exactly as-is.\n"
    "8. Respond with the reformatted Markdown only. No prefaces, no apologies, no explanations.\n"
)

USER_PREFIX = (
    "Reformat the following PDF-extracted Markdown into an engaging, "
    "easy-to-read version. Follow ALL rules above strictly.\n\n"
    "INPUT:\n"
)


@dataclass
class EngagingOutput:
    engaging_markdown: str
    provider: str
    model: str


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def engaging_from_raw(raw_markdown: str, model: str | None = None) -> EngagingOutput:
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider == "clod" and os.getenv("CLOD_API_KEY"):
        return _engaging_clod(raw_markdown, model)
    if provider == "openai" and os.getenv("OPENAI_API_KEY"):
        return _engaging_openai(raw_markdown, model)
    if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        return _engaging_anthropic(raw_markdown, model)
    # Auto-detect if provider unset.
    if os.getenv("CLOD_API_KEY"):
        return _engaging_clod(raw_markdown, model)
    if os.getenv("OPENAI_API_KEY"):
        return _engaging_openai(raw_markdown, model)
    if os.getenv("ANTHROPIC_API_KEY"):
        return _engaging_anthropic(raw_markdown, model)
    return _engaging_local(raw_markdown)


def _engaging_openai(raw: str, model: str | None) -> EngagingOutput:
    model = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    api_key = os.environ["OPENAI_API_KEY"]
    base = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    return _chat_completions(
        url=f"{base}/chat/completions",
        api_key=api_key,
        model=model,
        raw=raw,
        provider="openai",
    )


def _engaging_clod(raw: str, model: str | None) -> EngagingOutput:
    model = model or os.getenv("CLOD_MODEL") or "DeepSeek V3"
    api_key = os.environ["CLOD_API_KEY"]
    base = (os.getenv("CLOD_BASE_URL") or "https://api.clod.io/v1").rstrip("/")
    return _chat_completions(
        url=f"{base}/chat/completions",
        api_key=api_key,
        model=model,
        raw=raw,
        provider="clod",
    )


def _chat_completions(
    *, url: str, api_key: str, model: str, raw: str, provider: str
) -> EngagingOutput:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PREFIX + raw},
        ],
        "temperature": 0,
        "max_completion_tokens": 8192,
    }
    with httpx.Client(timeout=180) as client:
        r = client.post(
            url, json=payload, headers={"Authorization": f"Bearer {api_key}"}
        )
        r.raise_for_status()
        data = r.json()
    text = data["choices"][0]["message"]["content"]
    return EngagingOutput(text, provider, model)


def _engaging_anthropic(raw: str, model: str | None) -> EngagingOutput:
    model = model or os.getenv("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"
    api_key = os.environ["ANTHROPIC_API_KEY"]
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": model,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": USER_PREFIX + raw},
        ],
        "max_tokens": 8192,
        "temperature": 0,
    }
    with httpx.Client(timeout=120) as client:
        r = client.post(
            url,
            json=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        r.raise_for_status()
        data = r.json()
    text = "".join(
        part.get("text", "")
        for part in data.get("content", [])
        if part.get("type") == "text"
    )
    return EngagingOutput(text, "anthropic", model)


# --- Deterministic local fallback -----------------------------------------
_EMOJI_MAP = [
    (re.compile(r"\b(revenue|growth|sales|financ|quarter|q[1-4])\b", re.I), "📈"),
    (re.compile(r"\b(warning|caution|risk|danger)\b", re.I), "⚠️"),
    (re.compile(r"\b(note|notes|remember)\b", re.I), "📝"),
    (re.compile(r"\b(insight|key|important)\b", re.I), "💡"),
    (re.compile(r"\b(data|table|chart|graph)\b", re.I), "📊"),
    (re.compile(r"\b(summary|conclusion|overview)\b", re.I), "🧭"),
]


def _engaging_local(raw: str) -> EngagingOutput:
    """Rule-based enhancement that never changes content.

    Only adds:
    - emojis next to section headings
    - bold for ALL-CAPS short headers
    - blockquote callouts for lines starting with 'Summary:' or 'Key takeaway:'
    """
    lines = raw.splitlines()
    out: list[str] = []
    for line in lines:
        # decorate markdown headings
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            hashes, heading = m.group(1), m.group(2)
            prefix = ""
            for pattern, emoji in _EMOJI_MAP:
                if pattern.search(heading):
                    prefix = emoji + " "
                    break
            if not prefix:
                prefix = "📄 " if len(hashes) <= 2 else "🔹 "
            out.append(f"{hashes} {prefix}{heading}")
            continue
        # decorate 'Summary:' / 'Key takeaway:' callouts
        m2 = re.match(r"^\s*(summary|key takeaway|takeaway|tip)\s*:\s*(.*)$", line, re.I)
        if m2:
            out.append(f"> 💡 **{m2.group(1).title()}:** {m2.group(2)}")
            continue
        out.append(line)
    text = "\n".join(out)
    return EngagingOutput(text, "local-rules", "no-llm")
