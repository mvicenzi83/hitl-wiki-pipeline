"""
confidence_manager.py -- Script canonico per la gestione della confidence.

Per ogni pagina wiki:
  1. Conta le citazioni inbound ([[link]])
  2. Calcola il confidence score (formula con baseline per nuove pagine)
  3. Aggiorna il frontmatter YAML della pagina
  4. Genera il health report

Usage:
    python confidence_manager.py
"""

import re
from datetime import datetime
from pathlib import Path

from utils import (
    load_config,
    calculate_confidence,
    clean_and_update_properties,
    get_domains,
)


def run_confidence_manager(cfg: dict) -> int:
    conf_cfg  = cfg["confidence"]

    # Usa il primo dominio configurato (health_report opzionale per dominio)
    domains = get_domains(cfg)
    if not domains:
        print("[!] Nessun dominio configurato in config.toml.")
        return 0
    domain      = domains[0]
    wiki_path   = Path(domain["wiki"])
    report_path = Path(domain.get("health_report", str(wiki_path / "health-report.md")))
    threshold   = conf_cfg["threshold"]

    all_files = [f for f in wiki_path.glob("**/*.md") if f.name != "health-report.md"]

    # ------------------------------------------------------------------
    # 1. Lettura contenuti e conteggio citazioni inbound
    # ------------------------------------------------------------------
    citation_map = {f.name: 0 for f in all_files}
    content_map  = {}

    for file in all_files:
        content = file.read_text(encoding="utf-8")
        content_map[file.name] = content
        for link in re.findall(r"\[\[(.*?)\]\]", content):
            target = link.split("|")[0] + ".md"
            if target in citation_map:
                citation_map[target] += 1

    # ------------------------------------------------------------------
    # 2. Calcolo confidence e aggiornamento frontmatter
    # ------------------------------------------------------------------
    results = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for file in all_files:
        content    = content_map[file.name]
        date_match = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", content)
        date_str   = date_match.group(1) if date_match else today_str

        citations  = citation_map[file.name]
        word_count = len(content.split())

        conf = calculate_confidence(citations, date_str, word_count, cfg)

        updated_content = clean_and_update_properties(
            content,
            {"confidence_score": conf, "last_analyzed": today_str},
        )
        file.write_text(updated_content, encoding="utf-8")

        results.append({
            "name": file.name,
            "conf": conf,
            "cit":  citations,
            "date": date_str,
        })

    # ------------------------------------------------------------------
    # 3. Generazione Health Report
    # ------------------------------------------------------------------
    results.sort(key=lambda x: x["conf"])

    with open(report_path, "w", encoding="utf-8") as rf:
        rf.write("# Wiki Health Report\n")
        rf.write(f"**Last analyzed**: {today_str}\n\n")

        rf.write(f"## Zone Critiche (confidence < {threshold})\n\n")
        fragile = [r for r in results if r["conf"] < threshold]
        if not fragile:
            rf.write("Nessuna pagina sotto la soglia critica.\n")
        else:
            rf.write("| Pagina | Confidence | Citazioni | Ultimo Aggiornamento |\n")
            rf.write("| :--- | :---: | :---: | :---: |\n")
            for r in fragile:
                name = r["name"].replace(".md", "")
                rf.write(f"| [[{name}]] | {r['conf']} | {r['cit']} | {r['date']} |\n")

        rf.write("\n## Pilastri della Conoscenza (Top 5)\n\n")
        top_5 = sorted(results, key=lambda x: x["conf"], reverse=True)[:5]
        for i, r in enumerate(top_5, 1):
            name = r["name"].replace(".md", "")
            rf.write(f"{i}. [[{name}]] (Conf: {r['conf']})\n")

        rf.write("\n---\n*Report generato automaticamente dal Confidence Manager.*\n")

    return len(results)


if __name__ == "__main__":
    cfg = load_config()
    print("Avvio Confidence Manager...")
    count = run_confidence_manager(cfg)
    print(f"Processate {count} pagine. Health report aggiornato.")