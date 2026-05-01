# Plan: Hybrid Search (BM25 + Vector) per Wiki-LLM

**Data**: 2026-04-24  
**Progetto**: d:\obsidian_git  
**Stato**: In pianificazione

---

## TL;DR

Aggiungere hybrid search alla wiki-llm pipeline esistente: BM25 (keyword) + embeddings vettoriali (Ollama), fusi con RRF. Due nuovi script (`build_index.py`, `search_wiki.py`), config esteso, watcher integrato. Zero infrastruttura esterna — tutto locale, file-based.

---

## Decisioni

| Componente | Scelta | Motivazione |
|---|---|---|
| **Embedding model** | `embeddinggemma:latest` via Ollama | Modello embedding locale già disponibile nell'ambiente |
| **Storage** | File-based: BM25 pickle + numpy .npz + meta.json | Zero infrastruttura, coerente col resto del progetto |
| **Scope ricerca** | Tutta `wiki/` (ai + lytro + ideas) | Copertura completa della knowledge base |
| **Output** | CLI primario + flag `--md` per `search-results.md` | CLI = pattern del progetto; `--md` per integrazione Obsidian |
| **Rebuild trigger** | Automatico (watcher) + manuale (`python build_index.py`) | Flessibilità massima |
| **Algoritmo fusione** | RRF — Reciprocal Rank Fusion (k=60) | Robusto, nessun iperparametro da tuning, standard nel settore |

---

## Fase 1 — Config e fondamenta

### Step 1: Aggiungere sezione `[search]` a `pipeline/config.toml`

```toml
[search]
embedding_model = "embeddinggemma:latest"
index_dir = "D:/obsidian_git/pipeline/index"
bm25_k1 = 1.5
bm25_b = 0.75
rrf_k = 60
top_k = 5
rebuild_index_on_publish = true
wiki_dirs = [
  "D:/obsidian_git/wiki/ai",
  "D:/obsidian_git/wiki/lytro",
  "D:/obsidian_git/wiki/ideas"
]
exclude_files = ["health-report.md", "wiki-home.md", "index.md", "search-results.md", "log.md"]
```

### Step 2: Creare `pipeline/index/` directory

Aggiungere al `.gitignore`:
```
pipeline/index/bm25.pkl
pipeline/index/embeddings.npz
pipeline/index/meta.json
```

I file di indice sono artefatti generati — non vanno in git.

---

## Fase 2 — `build_index.py` (nuovo script)

Script standalone: scansiona wiki pages → costruisce BM25 index + embeddings.

### Flusso

1. Load config da `config.toml`
2. Scansiona tutti i `.md` nelle `wiki_dirs` (escludi `exclude_files`)
3. Per ogni pagina:
   - Strip YAML frontmatter (blocco tra `---`)
   - Estrai titolo (primo `# heading`)
   - Estrai summary (`**Summary**: ...`)
   - Estrai dominio (`**Domain**: ...`)
   - Strip `[[wiki-links]]` → testo pulito per indicizzazione
4. Build `BM25Okapi` (libreria `rank_bm25`) su corpus completo
5. Per ogni pagina: embedding via Ollama API `/api/embed` → vettore numpy
6. Salva in `pipeline/index/`:
   - `bm25.pkl` — indice BM25 serializzato (pickle)
   - `embeddings.npz` — matrice numpy (shape: `n_pages × embedding_dim`)
   - `meta.json` — lista `{id, path, title, summary, domain, last_modified}`
7. Stampa stats: n pagine indicizzate, tempo totale, dimensione file indice

### Edge cases

| Scenario | Comportamento |
|---|---|
| Pagina vuota o malformata | Skip con warning, continua |
| Ollama non disponibile | Fail immediato con messaggio chiaro e suggerimento |
| `index/` non esiste | Creata automaticamente |
| Nessuna wiki page trovata | Avviso e uscita pulita |

---

## Fase 3 — `search_wiki.py` (nuovo script)

CLI hybrid search.

### Usage

```
python search_wiki.py "query"
python search_wiki.py "query" --top 10
python search_wiki.py "query" --domain ai
python search_wiki.py "query" --md          # salva wiki/search-results.md
python search_wiki.py "query" --bm25-only   # solo keyword (debug)
python search_wiki.py "query" --vec-only    # solo vettoriale (debug)
```

### Algoritmo

```
Query
  │
  ├─→ [BM25]  tokenizza → scores su corpus → rank list r_bm25
  │
  └─→ [Vec]   embed via Ollama → cosine similarity → rank list r_vec
                                                              │
                                                        [RRF Fusion]
                                              score(d) = 1/(k + r_bm25(d))
                                                        + 1/(k + r_vec(d))
                                                              │
                                                        sort descending
                                                              │
                                                         Top-K results
```

### Flusso dettagliato

1. Load config + index files (`bm25.pkl`, `embeddings.npz`, `meta.json`)
2. Verifica che l'indice esiste → se no: `"Indice non trovato. Esegui: python build_index.py"`
3. Tokenizza query per BM25
4. Embed query via Ollama (stesso modello usato in build_index)
5. BM25: scores su tutti doc → ordina → assegna rank
6. Cosine similarity numpy → ordina → assegna rank
7. RRF: calcola score fuso per ogni documento
8. (Opzionale) Filtra per `--domain`
9. Output top-K

### Formato output CLI

```
Ricerca: "agent memory patterns"  [hybrid | top 5]

[1] Agent Memory Systems  (ai)   score: 0.0321
    wiki/ai/agent-memory-systems.md
    → "Tecniche per dotare gli agenti LLM di memoria persistente..."

[2] LangGraph  (ai)   score: 0.0289
    wiki/ai/langgraph.md
    → "Framework per orchestrare agenti con grafi di stato espliciti..."

...
```

### Output `--md` (search-results.md)

Genera `wiki/search-results.md` con tabella Obsidian:

```markdown
# Risultati ricerca

**Query**: "agent memory patterns"  
**Data**: 2026-04-24  **Modalità**: hybrid

| # | Pagina | Domain | Score | Summary |
|---|--------|--------|-------|---------|
| 1 | [[agent-memory-systems]] | ai | 0.0321 | Tecniche per... |
| 2 | [[langgraph]] | ai | 0.0289 | Framework per... |
```

---

## Fase 4 — Integrazione `wiki_watcher.py`

Modifica minima: dopo la chiamata a `confidence_manager.run()` (pubblicazione pagina):

```python
if config["search"].get("rebuild_index_on_publish", False):
    subprocess.run([sys.executable, "build_index.py"], cwd=PIPELINE_DIR)
    print("[INDEX] Indice aggiornato.")
```

- Flag `rebuild_index_on_publish` in `[search]` per abilitare/disabilitare
- Nessuna modifica al resto del watcher

---

## File coinvolti

| File | Tipo modifica |
|---|---|
| `pipeline/config.toml` | Aggiungere sezione `[search]` |
| `pipeline/build_index.py` | **Nuovo** |
| `pipeline/search_wiki.py` | **Nuovo** |
| `pipeline/wiki_watcher.py` | ~5 righe: trigger rebuild post-pubblicazione |
| `.gitignore` | Aggiungere file di indice |

---

## Dipendenze

### Nuove librerie Python

```
pip install rank-bm25
# numpy probabilmente già presente
```

### Setup one-time Ollama

```
ollama pull embeddinggemma:latest
```

---

## Checklist di verifica

- [ ] `ollama pull embeddinggemma:latest` — modello disponibile
- [ ] `pip install rank-bm25` — libreria installata
- [ ] `python build_index.py` — completa senza errori, crea i 3 file di indice
- [ ] `python search_wiki.py "test query"` — restituisce risultati ranked
- [ ] `python search_wiki.py "query" --md` — genera `wiki/search-results.md`
- [ ] `python search_wiki.py "query" --bm25-only` — solo BM25 funziona
- [ ] `python search_wiki.py "query" --vec-only` — solo vettoriale funziona
- [ ] Pubblica una pagina → watcher fa rebuild automatico → log `[INDEX]`
- [ ] Confronto qualitativo: hybrid > bm25-only su query semantiche

---

## Note e domande aperte

- **Aggiornamento incrementale**: per ora rebuild completo ad ogni pubblicazione — accettabile con wiki piccola. Da valutare rebuild incrementale se la wiki cresce molto.
- **Normalizzazione score RRF**: i valori RRF sono in range `(0, 1/k]` — già comparabili, nessuna normalizzazione necessaria.
- **Stemming/tokenizzazione BM25**: `rank_bm25` usa split su whitespace di default. Valutare tokenizer italiano/inglese se la qualità BM25 è bassa su query multilingua.
- **Embedding dim**: da verificare per `embeddinggemma:latest` al primo run di `build_index.py` — con ~100 pagine wiki la matrice sarà comunque trascurabile.
