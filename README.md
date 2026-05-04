# HITL Wiki Pipeline

> An automated knowledge-base pipeline inspired by [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), extended with automated entity extraction, LLM-assisted draft generation, and a frictionless human-in-the-loop approval flow driven entirely by **Obsidian checkboxes**.

---

## The Idea

Karpathy's original insight is correct: **stop re-deriving, start compiling.** A wiki accumulates and compounds; RAG retrieves and forgets.

This project builds on that foundation with lessons learned from running the pattern in practice:

- Manual bookkeeping is the main reason wikis rot — so we automate it
- Not all LLM-generated content should be trusted — so we add a human gate
- The human gate should be frictionless — so we implement it as **Obsidian checkboxes**

The result: drop a Markdown file into `/raw/ai` and the pipeline eventually produces a reviewed, published wiki page in `/wiki/ai` — with you in control at every meaningful decision point.

---

## How It Works

```
raw/ai/source.md
      │
      ▼
[1] extract_candidates.py
      │  Extracts entities (CamelCase, kebab-case, contextual patterns)
      │  → candidates/candidates_source.json
      │
      ▼
[2] ingest_assistant.py
      │  Generates a proposal Markdown file with checkboxes
      │  → pending_ingests/proposal_source.md
      │
      ▼  ← Human reviews in Obsidian: ticks entities to ingest
      │     ticks ✅ AVVIA INGEST to confirm
      │
      ▼  [GATE 1]
[3] process_proposal.py
      │  Reads approved entities, calls LLM for each one
      │  → drafts/draft_entity.md  (with sentinel checkbox at bottom)
      │
      ▼  ← Human reviews draft in Obsidian: reads content
      │     ticks ✅ APPROVA BOZZA to approve
      │
      ▼  [GATE 2]
[4] approve_draft_auto.py
      │  Strips metadata, writes clean page to wiki/ai/
      └  → wiki/ai/entity.md  +  updates confidence scores
```

`wiki_watcher.py` orchestrates everything — it monitors all three directories simultaneously and fires the correct script on each file event.

---

## Prerequisites

- **Python 3.11+** (uses `tomllib` from stdlib — no extra TOML dependency)
- **Obsidian** (optional, but the checkbox-based HITL flow is designed for it)
- A local or cloud LLM:
  - [Ollama](https://ollama.com/) running locally (recommended for privacy)
  - Any OpenAI-compatible API: Google AI Studio, OpenRouter, etc.

---

## Installation

```bash
git clone https://github.com/mvicenzi83/hitl-wiki-pipeline.git
cd hitl-wiki-pipeline
pip install -r requirements.txt
```

---

## Configuration

Copy the example config and fill in your paths and LLM settings:

```bash
cp config.toml.example config.toml
```

Then edit `config.toml`. The most important sections:

### Paths

```toml
[paths]
raw_base       = "./raw"          # where your source .md files live
wiki_base      = "./wiki"         # where published wiki pages go
proposals_dir  = "./pending_ingests"
candidates_dir = "./candidates"
```

### Domains

One `[[domains]]` block per knowledge domain (e.g. `ai`, `lytro`):

```toml
[[domains]]
name          = "ai"
raw           = "./raw/ai"
wiki          = "./wiki/ai"
health_report = "./wiki/ai/health-report.md"
```

### LLM

```toml
[llm]
api_format  = "openai"                    # "openai" or "ollama"
base_url    = "http://localhost:11434/v1" # Ollama local
model       = "gemma3:27b"
api_key_env = "OLLAMA_API_KEY"            # env var name for your API key
drafts_dir  = "./drafts"
```

### Chat inbox

To use `chat_responder.py`, create a `chat/inbox.md` file and add entries in this format:

```markdown
**Q:** What is the key argument in The Bitter Lesson?

**A:** *(in attesa...)*
```

Run `python chat_responder.py` — it searches your wiki for relevant pages, injects them as context, and writes the answer in-place.

```toml
[chat]
inbox_path         = "./chat/inbox.md"
wiki_context       = true   # inject wiki pages as context
wiki_context_pages = 3      # max pages injected per query
```

For **cloud providers**, set your key in the environment before running:

```bash
# Google AI Studio
export GOOGLE_API_KEY=AIza...
# OpenRouter
export OPENROUTER_API_KEY=sk-or-...
```

---

## Usage

### Automatic (recommended)

Start the file watcher. It monitors `raw/`, `pending_ingests/`, and `drafts/` and triggers the correct pipeline step automatically:

```bash
python wiki_watcher.py
# or on Windows:
run_watcher.bat
```

Then **just drop a `.md` file into `raw/ai/`** — the pipeline starts automatically.

### Manual (step by step)

```bash
# Step 1 — Extract entity candidates from a source file
python extract_candidates.py raw/ai/my-source.md

# Step 2 — Generate a proposal with checkboxes
python ingest_assistant.py raw/ai/my-source.md

# Step 3 — (In Obsidian: tick entities + ✅ AVVIA INGEST)
# Then trigger draft generation manually:
python process_proposal.py pending_ingests/proposal_my-source.md

# Step 4 — (In Obsidian: read draft + tick ✅ APPROVA BOZZA)
# Or use the interactive terminal fallback:
python approve_drafts.py

# Update confidence scores and health report
python confidence_manager.py

# Run a structural health check on your wiki
python wiki_maintenance.py
python wiki_maintenance.py --full   # + LLM-assisted accuracy check

# Apply checked fixes from the maintenance report (rename links, add related pages)
python apply_maintenance.py          # dry-run preview
python apply_maintenance.py --apply  # apply for real

# Ask the LLM a question using your wiki as context (requires chat/inbox.md)
python chat_responder.py
```

---

## Project Structure

| File | Role |
| :--- | :--- |
| `config.toml.example` | Template — copy to `config.toml` and configure |
| `utils.py` | Shared library: config loading, entity extraction, confidence formula, fuzzy matching, frontmatter handling |
| `extract_candidates.py` | Phase 1: entity extraction → JSON candidates |
| `ingest_assistant.py` | Phase 2: proposal Markdown with review checkboxes |
| `llm_bridge.py` | LLM client: generates wiki drafts from candidates (Ollama native + OpenAI-compatible) |
| `process_proposal.py` | Gate 1: reads approved entities from proposal, triggers LLM drafts |
| `approve_draft_auto.py` | Gate 2: publishes approved draft to wiki, updates confidence scores |
| `approve_drafts.py` | Manual fallback: interactive terminal review (approve / reject / skip) |
| `confidence_manager.py` | Scores every wiki page, generates health report |
| `wiki_watcher.py` | Orchestrator: monitors all directories, fires scripts on file events |
| `wiki_maintenance.py` | Read-only wiki audit: broken links, orphan pages, bidirectionality, LLM accuracy check |
| `apply_maintenance.py` | Applies checked actions from the maintenance report (rename links, add Related pages) with dry-run support |
| `chat_responder.py` | Answers pending queries in `chat/inbox.md` using the LLM + wiki pages as context |
| `run_watcher.bat` | One-click Windows launcher |
| `docs/user-guide.md` | Practical how-to guide: "I want to do X, what do I run?" |
| `docs/` | Design documents and implementation plans |

---

## Wiki Page Format

Every generated page follows this structure:

```markdown
# Page Title

**Summary**: One or two sentences describing this page.

**Domain**: ai

**Sources**: [[source-page]]

**Last updated**: YYYY-MM-DD

---

Main content here.

## Related pages

- [[related-page-1]]
- [[related-page-2]]
```

---

## License

MIT — see [LICENSE](LICENSE).
