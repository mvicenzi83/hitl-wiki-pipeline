"""
wiki_watcher.py — Monitora raw/ e wiki/ in tempo reale.

Comportamento:
  - Nuovo file .md in /raw/<dominio> → ingest_assistant.py processato SOLO su quel file
  - Dopo ogni ingest → confidence_manager.py aggiorna scores e health report
  - Nuova directory in /raw/ → replicata in wiki/; se dominio top-level, aggiunta al config
  - Directory rimossa da /raw/ → solo avviso, wiki non toccata

Usage:
    python wiki_watcher.py
    (oppure tramite run_watcher.bat)
"""

import sys
import time
import subprocess
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from utils import load_config, get_domains

# Percorso canonico degli script — relativo a questo file
_HERE = Path(__file__).parent
ASSISTANT_SCRIPT        = _HERE / "ingest_assistant.py"
MANAGER_SCRIPT          = _HERE / "confidence_manager.py"
PROCESS_PROPOSAL_SCRIPT = _HERE / "process_proposal.py"
APPROVE_DRAFT_SCRIPT    = _HERE / "approve_draft_auto.py"

# -----------------------------------------------------------------------
# Debounce condiviso tra tutti gli handler
# -----------------------------------------------------------------------
_last_triggered: dict[str, float] = {}
_DEBOUNCE_SECONDS = 3.0


def _debounce_check(path: str) -> bool:
    """Ritorna True se il path può essere processato (debounce ok)."""
    now = time.time()
    if now - _last_triggered.get(path, 0) < _DEBOUNCE_SECONDS:
        return False
    _last_triggered[path] = now
    return True


class WikiEventHandler(FileSystemEventHandler):
    """
    Gestisce gli eventi del filesystem su /raw/<dominio>.
    - on_created: nuovo file .md
    - on_moved:   file spostato dentro la directory monitorata
    """

    def __init__(self, domain_name: str):
        super().__init__()
        self.domain_name = domain_name

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._handle_new_file(event.src_path)

    def on_moved(self, event):
        if not event.is_directory and event.dest_path.endswith(".md"):
            self._handle_new_file(event.dest_path)

    def _handle_new_file(self, file_path: str) -> None:
        name = Path(file_path).name
        print(f"[+] [{self.domain_name}] Nuovo file rilevato: {name}")
        print(f"    Avvio Ingest Assistant per questo file...")
        try:
            # Passa il path del singolo file — evita di rielaborare tutta la directory
            subprocess.run(
                [sys.executable, str(ASSISTANT_SCRIPT), file_path],
                check=True,
            )
            print(f"    Proposal generata per: {name}")
            self._refresh_confidence()
        except subprocess.CalledProcessError as e:
            print(f"[!] Errore nell'Ingest Assistant: {e}")

    def _refresh_confidence(self) -> None:
        print("    Aggiornamento confidence scores...")
        try:
            subprocess.run([sys.executable, str(MANAGER_SCRIPT)], check=True)
            print("    Health report aggiornato.")
        except subprocess.CalledProcessError as e:
            print(f"[!] Errore nel Confidence Manager: {e}")


class ProposalEventHandler(FileSystemEventHandler):
    """Monitora pending_ingests/ — triggera process_proposal.py su on_modified."""

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            if _debounce_check(event.src_path):
                self._handle_modified(event.src_path)

    def _handle_modified(self, file_path: str) -> None:
        name = Path(file_path).name
        print(f"[~] Proposal modificata: {name}")
        print(f"    Controllo sentinel AVVIA INGEST...")
        try:
            subprocess.run(
                [sys.executable, str(PROCESS_PROPOSAL_SCRIPT), file_path],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[!] Errore in process_proposal: {e}")


class DraftEventHandler(FileSystemEventHandler):
    """Monitora drafts/ (non recursive) — triggera approve_draft_auto.py su on_modified."""

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            if "approved" not in event.src_path and _debounce_check(event.src_path):
                self._handle_modified(event.src_path)

    def _handle_modified(self, file_path: str) -> None:
        name = Path(file_path).name
        print(f"[~] Bozza modificata: {name}")
        print(f"    Controllo sentinel APPROVA BOZZA...")
        try:
            subprocess.run(
                [sys.executable, str(APPROVE_DRAFT_SCRIPT), file_path],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"[!] Errore in approve_draft_auto: {e}")


if __name__ == "__main__":
    cfg            = load_config()


class RawStructureEventHandler(FileSystemEventHandler):
    """
    Monitora raw_base/ per eventi su directory.
    - Nuova directory  → replicata in wiki/; se dominio top-level, aggiunta al config.toml
    - Directory rimossa → solo avviso, wiki non toccata
    """

    def __init__(self, raw_base: Path, wiki_base: Path, config_path: Path):
        super().__init__()
        self.raw_base    = raw_base
        self.wiki_base   = wiki_base
        self.config_path = config_path

    def on_created(self, event):
        if event.is_directory and _debounce_check(event.src_path):
            self._mirror_directory(Path(event.src_path))

    def on_deleted(self, event):
        if event.is_directory and _debounce_check(event.src_path):
            try:
                rel = Path(event.src_path).relative_to(self.raw_base)
                print(f"[WARN] raw/{rel} rimossa — wiki/{rel} non toccata.")
            except ValueError:
                pass

    def _mirror_directory(self, new_dir: Path) -> None:
        try:
            rel = new_dir.relative_to(self.raw_base)
        except ValueError:
            return
        wiki_target = self.wiki_base / rel
        wiki_target.mkdir(parents=True, exist_ok=True)
        print(f"[+] [mirror] raw/{rel} → wiki/{rel} creata")
        if len(rel.parts) == 1:
            self._register_domain(rel.parts[0], new_dir, wiki_target)

    def _register_domain(self, name: str, raw_path: Path, wiki_path: Path) -> None:
        config_text = self.config_path.read_text(encoding="utf-8")
        if f'name = "{name}"' in config_text:
            print(f"  [skip] Dominio '{name}' già registrato in config.toml")
            return
        new_entry = (
            f'\n[[domains]]\n'
            f'name = "{name}"\n'
            f'raw  = "{raw_path.as_posix()}"\n'
            f'wiki = "{wiki_path.as_posix()}"\n'
        )
        with open(self.config_path, "a", encoding="utf-8") as f:
            f.write(new_entry)
        print(f"  [config] Dominio '{name}' aggiunto a config.toml")


if __name__ == "__main__":
    cfg            = load_config()
    domains        = get_domains(cfg)
    raw_base       = Path(cfg["paths"]["raw_base"])
    wiki_base      = Path(cfg["paths"]["wiki_base"])
    proposals_path = Path(cfg["paths"]["proposals_dir"])
    drafts_path    = Path(cfg["llm"]["drafts_dir"])
    config_path    = Path(__file__).parent / "config.toml"

    print("Wiki Watcher avviato")
    for d in domains:
        print(f"Monitoring raw [{d['name']}]: {d['raw']}")
    print(f"Monitoring proposals: {proposals_path}")
    print(f"Monitoring drafts:    {drafts_path}")
    print(f"Monitoring struttura: {raw_base}")
    print("------------------------------------------------------")
    print("1. Aggiungi un .md in /raw/<dominio>     -> Ingest Assistant (solo quel file)")
    print("2. Spunta sentinel in proposal           -> Process Proposal (entità selezionate)")
    print("3. Spunta sentinel in bozza              -> Approve Draft (pubblica in wiki)")
    print("4. Dopo ogni publish                     -> Confidence Manager si aggiorna")
    print("5. Nuova directory in raw/               -> Mirroring automatico in wiki/")
    print("------------------------------------------------------")
    print("Ctrl+C per fermare.")

    observer = Observer()
    for domain in domains:
        observer.schedule(WikiEventHandler(domain["name"]), domain["raw"], recursive=True)
    observer.schedule(ProposalEventHandler(), str(proposals_path), recursive=False)
    observer.schedule(DraftEventHandler(),    str(drafts_path),    recursive=False)
    observer.schedule(
        RawStructureEventHandler(raw_base, wiki_base, config_path),
        str(raw_base),
        recursive=True,
    )

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nWiki Watcher fermato.")

    observer.join()
    raw_path       = Path(cfg["paths"]["raw_ai"])
    proposals_path = Path(cfg["paths"]["proposals_dir"])
    drafts_path    = Path(cfg["llm"]["drafts_dir"])

    print("Wiki Watcher avviato")
    print(f"Monitoring raw:       {raw_path}")
    print(f"Monitoring proposals: {proposals_path}")
    print(f"Monitoring drafts:    {drafts_path}")
    print("------------------------------------------------------")
    print("1. Aggiungi un .md in /raw/ai        -> Ingest Assistant (solo quel file)")
    print("2. Spunta sentinel in proposal       -> Process Proposal (entit\u00e0 selezionate)")
    print("3. Spunta sentinel in bozza          -> Approve Draft (pubblica in wiki/ai)")
    print("4. Dopo ogni publish                 -> Confidence Manager si aggiorna")
    print("------------------------------------------------------")
    print("Ctrl+C per fermare.")

    observer = Observer()
    observer.schedule(WikiEventHandler(),     str(raw_path),       recursive=False)
    observer.schedule(ProposalEventHandler(), str(proposals_path), recursive=False)
    observer.schedule(DraftEventHandler(),    str(drafts_path),    recursive=False)

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nWiki Watcher fermato.")

    observer.join()
