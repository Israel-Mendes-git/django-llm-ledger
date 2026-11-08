"""The store's support assistant: retrieve the relevant help sections, ask the LLM, cite sources.

Retrieval compares embeddings from a small local model (all-minilm through Ollama, 46 MB) so
"Can I get my money back?" finds "Refund timing" without sharing a word with it. With a few dozen
sections the vectors live in memory; use pgvector or similar when the documents grow. If Ollama
is not reachable, a keyword scorer (TF-IDF style) takes over.
"""

import logging
import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

from llm_ledger import LLMClient, llm
from llm_ledger.providers import OllamaProvider, build_provider
from llm_ledger import conf

logger = logging.getLogger(__name__)

DOCS_DIR = Path(__file__).resolve().parent / "docs"
TOP_K = 3
EMBED_MODEL = os.environ.get("DEMO_EMBED_MODEL", "all-minilm")
OLLAMA_URL = os.environ.get("DEMO_OLLAMA_URL", "http://localhost:11434")
# Cosine similarity: below MIN_SIMILARITY a section is unrelated (off-topic questions score
# under 0.25 with all-minilm); sections further than MAX_GAP from the best one are dropped.
MIN_SIMILARITY = 0.35
MAX_GAP = 0.08
# Follow-ups search twice (alone and with the previous question). The combined query leans toward
# the earlier topic, so the section the new detail needs scores further below the best one.
FOLLOWUP_MAX_GAP = 0.15
# ...but only when the question on its own is at least loosely about the help center. Otherwise a
# change of subject ("Do you sell tea?" after shipping) would drag the old topic's sections along.
FOLLOWUP_MIN_SIMILARITY = 0.30
# Drop sections scoring below this share of the best match, so weak keyword overlaps
# ("ships" in the boleto section) do not show up as sources.
RELATIVE_CUTOFF = 0.6

SYSTEM_PROMPT = """You are the customer support assistant of Serra Verde Coffee Roasters, an online coffee shop.
Answer ONLY with information from the numbered help sections provided. Be short and friendly (2 to 4 sentences).
Cite the sections you used like [1] or [2].
If the sections do not contain the answer, say you don't know and suggest using the "Talk to a human" button.
Never invent prices, dates, policies or promises.
A rule in a section applies only to the cases it names: a price or deadline for Brazil does not apply to other countries.
Earlier answers in the conversation may be about a different case, so check the sections again for the latest question."""

STOPWORDS = set("""
a an and are as at be by can do does for from have how i if in is it its my of on or our the their
this to we what when where which who will with you your yes not no any about after before into than there here
""".split())


@dataclass
class Section:
    doc: str
    title: str
    text: str

    @property
    def label(self):
        return f"{self.doc} › {self.title}"


def tokens(text):
    return [w for w in re.findall(r"[a-zà-ú0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS]


def stem(word):
    # Crude plural folding so "returns" matches "return" and "bags" matches "bag".
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


@lru_cache(maxsize=1)
def load_sections():
    sections = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        doc_title, current, lines = path.stem, None, []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                doc_title = line[2:].strip()
            elif line.startswith("## "):
                if current:
                    sections.append(Section(doc_title, current, " ".join(lines).strip()))
                current, lines = line[3:].strip(), []
            elif line.strip():
                lines.append(line.strip())
        if current:
            sections.append(Section(doc_title, current, " ".join(lines).strip()))
    return sections


@lru_cache(maxsize=1)
def _index():
    sections = load_sections()
    docs = [{stem(t) for t in tokens(f"{s.doc} {s.title} {s.title} {s.text}")} for s in sections]
    df = {}
    for terms in docs:
        for term in terms:
            df[term] = df.get(term, 0) + 1
    n = len(docs)
    idf = {term: math.log((n + 1) / (count + 0.5)) for term, count in df.items()}
    return sections, docs, idf


def _embed(texts):
    response = httpx.post(f"{OLLAMA_URL}/api/embed", json={"model": EMBED_MODEL, "input": texts}, timeout=30)
    response.raise_for_status()
    return response.json()["embeddings"]


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


_section_vectors = None


def _vectors():
    """Embed every section once per process. Failures are not cached, so Ollama can come back."""
    global _section_vectors
    if _section_vectors is None:
        _section_vectors = _embed([f"{s.doc} - {s.title}: {s.text}" for s in load_sections()])
    return _section_vectors


def _scored(queries):
    """Every section with its best similarity over the queries, best first."""
    vectors = _vectors()
    best = {}
    for query in _embed(list(queries)):
        for vector, section in zip(vectors, load_sections()):
            score = _cosine(query, vector)
            if score > best.get(section.label, (0, None))[0]:
                best[section.label] = (score, section)
    return sorted(best.values(), key=lambda pair: pair[0], reverse=True)


def retrieve(question, k=TOP_K, *, previous=None):
    """The help sections that answer the question, best first (may be empty).

    ``previous`` is the user's earlier question in the conversation, for follow-ups.
    """
    try:
        scored = _scored([question])
        if previous and scored and scored[0][0] >= FOLLOWUP_MIN_SIMILARITY:
            scored = _scored([question, f"{previous} {question}"])
        else:
            previous = None
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Embeddings unavailable (%s); using keyword search.", exc)
        return retrieve_keywords(question, k)
    if not scored or scored[0][0] < MIN_SIMILARITY:
        return []
    best, gap = scored[0][0], (FOLLOWUP_MAX_GAP if previous else MAX_GAP)
    return [s for score, s in scored[:k] if score >= MIN_SIMILARITY and best - score <= gap]


def retrieve_keywords(question, k=TOP_K):
    sections, docs, idf = _index()
    query = {stem(t) for t in tokens(question)}
    scored = []
    for section, terms in zip(sections, docs):
        score = sum(idf[t] for t in query & terms)
        if score > 0:
            scored.append((score, section))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return []
    best = scored[0][0]
    return [section for score, section in scored[:k] if score >= best * RELATIVE_CUTOFF]


def build_prompt(question, sections):
    if sections:
        context = "\n\n".join(f"[{i}] {s.label}\n{s.text}" for i, s in enumerate(sections, 1))
    else:
        context = "(No help section covers this question. Say you don't know and suggest the \"Talk to a human\" button.)"
    return f"Help sections:\n\n{context}\n\nCustomer question: {question}"


def client_for(simulate_outage=False):
    """The configured chain, optionally with a dead provider in front to show retries + fallback."""
    if not simulate_outage:
        return llm
    dead = OllamaProvider("primary-model", base_url="http://127.0.0.1:9", timeout=2)
    return LLMClient([dead, *[build_provider(p) for p in conf.get("PROVIDERS")]])


def answer(question, *, user=None, simulate_outage=False):
    sections = retrieve(question)
    result = client_for(simulate_outage).complete(
        build_prompt(question, sections),
        system=SYSTEM_PROMPT,
        feature="support_chat",
        user=user,
        max_tokens=200,
        use_cache=not simulate_outage,
    )
    return result, sections


HISTORY_TURNS = 6  # messages of earlier conversation sent along (3 questions and 3 answers)


def clean_history(history):
    """Keep the last few well-formed turns from the browser, trimmed, starting with a question."""
    turns = [
        {"role": m["role"], "content": m["content"][:1000]}
        for m in (history or [])
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ][-HISTORY_TURNS:]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def conversation(question, sections, history):
    """Earlier turns as they were, then this question with the help sections attached.

    Only the current turn carries sections, so earlier answers are not re-grounded on documents
    picked for a different question.
    """
    return [*history, {"role": "user", "content": build_prompt(question, sections)}]


def answer_stream(question, *, user=None, simulate_outage=False, history=None):
    """Same as ``answer`` but returns an LLMStream; iterate it for the text as it is written.

    ``history`` is the conversation so far. Retrieval also looks at the previous question, so a
    follow-up like "And to Portugal?" still finds the shipping sections.
    """
    history = clean_history(history)
    previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    # A follow-up often names only the new detail; the previous question carries the topic.
    sections = retrieve(question, previous=previous or None)
    stream = client_for(simulate_outage).stream(
        conversation(question, sections, history),
        system=SYSTEM_PROMPT,
        feature="support_chat",
        user=user,
        max_tokens=200,
        use_cache=not simulate_outage,
    )
    return stream, sections


def answer_text(question):
    """Entry point for evaluation suites: a question, or a conversation ending in one, in; text out."""
    if isinstance(question, list):
        *history, last = question
        stream, _ = answer_stream(last["content"], history=history)
        return "".join(stream)
    result, _ = answer(question)
    return result.text
