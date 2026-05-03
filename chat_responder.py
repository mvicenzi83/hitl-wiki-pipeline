"""
chat_responder.py — Risponde alle query pendenti in chat/inbox.md via LLM locale.

Comportamento:
  - Legge il file inbox (default: chat/inbox.md)
  - Trova le entry con **A:** *(in attesa...)*
  - Per ogni entry: cerca contesto rilevante nel wiki, chiama LLM, scrive risposta
  - Salva il file aggiornato in-place

Il wiki_context cerca nei file wiki le pagine più rilevanti per la domanda
(keyword match su nome file + prime righe) e le inietta come contesto nel prompt.

Usage:
    python chat_responder.py                        # Usa inbox_path dal config.toml
    python chat_responder.py chat/inbox.md          # File specifico
"""

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from utils import load_config
from llm_bridge import call_llm

# ---------------------------------------------------------------------------
# Costanti
# ---------------------------------------------------------------------------

_HERE           = Path(__file__).parent
PENDING_MARKER  = r"\*\(in attesa\.\.\.\)\*"

PROMPT_CHAT = """\
Sei un assistente AI personale. Rispondi alla domanda in modo chiaro, preciso e conciso.
{wiki_context_section}
Domanda: {question}

Rispondi direttamente. Usa la stessa lingua della domanda."""


# ---------------------------------------------------------------------------
# Wiki context
# ---------------------------------------------------------------------------

_SYSTEM_PAGES = {
    "health-report", "maintenance-report", "domains",
    "wiki-home", "log", "index",
}


# Parole comuni italiane e inglesi da ignorare nel keyword matching
_STOPWORDS = {
    "sono", "cosa", "come", "quando", "dove", "quale", "quali", "questo",
    "questa", "questi", "queste", "anche", "pero", "quindi", "perche",
    "perché", "essere", "avere", "fare", "dire", "quello", "della", "dello",
    "degli", "delle", "nella", "nello", "negli", "nelle", "dalla", "dallo",
    "dagli", "dalle", "alla", "allo", "agli", "alle", "that", "this", "with",
    "from", "what", "when", "where", "which", "have", "been", "will", "they",
    "their", "would", "could", "should", "about", "more", "than", "into",
    "some", "other", "used", "using", "each", "such", "also",
}


def _find_wiki_context(question: str, wiki_base: Path, max_pages: int) -> str:
    """
    Cerca nel wiki le pagine più rilevanti per la domanda tramite keyword matching.
    Filtra stopwords italiane/inglesi per migliorare la precisione.
    Ritorna una stringa di contesto formattata, o "" se nulla di rilevante.
    """
    words = set(re.findall(r"\b\w{4,}\b", question.lower())) - _STOPWORDS
    if not words:
        return ""

    # Boost: parole presenti nel nome del file valgono doppio
    def _score(md_file: Path) -> int:
        stem_words = set(md_file.stem.replace("-", " ").lower().split())
        stem_hits = sum(2 for w in words if w in stem_words)
        body_hits = sum(1 for w in words if w in body_cache[md_file])
        return stem_hits + body_hits

    # Pre-carica i body una sola volta
    body_cache: dict[Path, str] = {}
    candidates = []
    for md_file in wiki_base.rglob("*.md"):
        if md_file.stem in _SYSTEM_PAGES:
            continue
        try:
            body_cache[md_file] = md_file.read_text(encoding="utf-8")[:500].lower()
            candidates.append(md_file)
        except OSError:
            continue

    scores = [(s, f) for f in candidates if (s := _score(f)) > 0]
    if not scores:
        return ""

    scores.sort(key=lambda x: -x[0])
    sections = []
    for _, page in scores[:max_pages]:
        try:
            content = page.read_text(encoding="utf-8")[:1500]
            sections.append(f"--- [{page.stem}] ---\n{content}\n")
        except OSError:
            continue

    if not sections:
        return ""

    joined = "\n".join(sections)
    return f"\nContesto dalla knowledge base personale:\n{joined}\n"


# ---------------------------------------------------------------------------
# Git sync
# ---------------------------------------------------------------------------

def _git_push(inbox_path: Path, count: int) -> None:
    """Committa e pusha il file inbox aggiornato su GitHub."""
    repo_root = inbox_path.parent.parent  # chat/ -> repo root
    ts        = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg       = f"chat: {count} risposta/e automatica/e [{ts}]"
    try:
        subprocess.run(["git", "-C", str(repo_root), "add", str(inbox_path)], check=True)
        subprocess.run(["git", "-C", str(repo_root), "commit", "-m", msg],    check=True)
        subprocess.run(["git", "-C", str(repo_root), "push"],                  check=True)
        print(f"  [git] Push completato: {msg}")
    except subprocess.CalledProcessError as e:
        print(f"  [git] Attenzione: push fallito ({e}). La risposta è comunque salvata localmente.")


# ---------------------------------------------------------------------------
# Logica principale
# ---------------------------------------------------------------------------

_QUERY_PATTERN = re.compile(
    r"\*\*Q:\*\* ([^\n]+)\n\n\*\*A:\*\* \*\(in attesa[\s.…]+\)\*",
)


def process_inbox(inbox_path: Path, cfg: dict) -> int:
    """
    Processa un file inbox. Ritorna il numero di query a cui è stata data risposta.
    """
    chat_cfg  = cfg.get("chat", {})
    wiki_base = Path(cfg["paths"]["wiki_base"])
    use_wiki  = chat_cfg.get("wiki_context", True)
    max_pages = int(chat_cfg.get("wiki_context_pages", 3))

    text = inbox_path.read_text(encoding="utf-8")

    count = 0

    # Testo placeholder del template — da NON processare mai
    _TEMPLATE_QUESTIONS = {"scrivi qui la tua domanda"}

    def replace_match(m: re.Match) -> str:
        nonlocal count
        question = m.group(1).strip()
        if question.lower() in _TEMPLATE_QUESTIONS:
            return m.group(0)  # lascia invariato
        print(f"  [chat] Rispondo a: {question[:80]}{'...' if len(question) > 80 else ''}")

        wiki_section = _find_wiki_context(question, wiki_base, max_pages) if use_wiki else ""

        prompt = PROMPT_CHAT.format(
            wiki_context_section=wiki_section,
            question=question,
        )

        try:
            answer = call_llm(prompt, cfg)
        except Exception as e:
            answer = f"*(errore LLM: {e})*"

        count += 1
        return f"**Q:** {question}\n\n**A:** {answer}"

    new_text = _QUERY_PATTERN.sub(replace_match, text)

    if count > 0:
        inbox_path.write_text(new_text, encoding="utf-8")
        print(f"  [chat] {count} risposta/e scritta/e → {inbox_path.name}")
        _git_push(inbox_path, count)
    else:
        print("  [chat] Nessuna query pendente trovata.")

    return count


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg      = load_config()
    chat_cfg = cfg.get("chat", {})

    if len(sys.argv) > 1:
        inbox_path = Path(sys.argv[1])
    else:
        inbox_path = Path(chat_cfg.get("inbox_path", "D:/obsidian_git/chat/inbox.md"))

    if not inbox_path.exists():
        print(f"[!] File non trovato: {inbox_path}")
        sys.exit(1)

    print(f"[chat_responder] Elaborazione: {inbox_path}")
    processed = process_inbox(inbox_path, cfg)
    print(f"[chat_responder] Completato. Query risposte: {processed}")
