"""Nacteni config.json. Adresy ani parametry modelu nepatri do kodu."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8420},
    "lm_studio": {
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "google/gemma-3-12b-it-qat",
        "context": 8192,
        "timeout_s": 900,
    },
    "inference": {
        "temperature": 0.3,
        "top_p": 0.9,
        "repeat_penalty": 1.05,
        "seed": 606169,
        "max_tokens": 4096,
    },
    "batching": {"target_source_tokens": 1200, "chars_per_token": 4.0,
                 "max_segments": 25},
    "glossary": {"chunk_source_tokens": 2500, "terms_per_request": 15,
                 "min_occurrences": 2},
    "paths": {"projects_dir": "projects"},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    raw = {}
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = _merge(DEFAULTS, raw)
    projects = Path(cfg["paths"]["projects_dir"])
    if not projects.is_absolute():
        projects = ROOT / projects
    cfg["paths"]["projects_dir"] = str(projects)
    return cfg


CFG = load()
PROJECTS_DIR = Path(CFG["paths"]["projects_dir"])


def save(updates):
    """Zapise zmeny do config.json a promitne je do bezici aplikace.

    CFG je jeden a tentyz slovnik ve vsech modulech, takze se prepise na miste
    a nova hodnota plati okamzite i tam, kde uz je naimportovany.
    """
    raw = {}
    if CONFIG_PATH.exists():
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    merged = _merge(raw, updates)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    fresh = load()
    CFG.clear()
    CFG.update(fresh)
    return CFG
