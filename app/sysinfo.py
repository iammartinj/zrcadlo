"""Cteni skutecneho stavu stroje. Nic se nedomysli.

Kdyz udaj nejde precist, vraci se None a rozhrani ten radek vynecha.
"""
import subprocess

import httpx

NVIDIA_QUERY = [
    "nvidia-smi",
    "--query-gpu=memory.total,memory.used,name",
    "--format=csv,noheader,nounits",
]

# trust_env=False: pripadna promenna HTTP_PROXY by lokalni provoz poslala ven
_CLIENT = httpx.Client(trust_env=False, timeout=5.0)


def gpu_memory():
    """Vrati {total_mb, used_mb, name} z nvidia-smi, nebo None."""
    try:
        out = subprocess.run(NVIDIA_QUERY, capture_output=True, text=True,
                             timeout=4, creationflags=_no_window())
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    line = out.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 2:
        return None
    try:
        return {"total_mb": int(float(parts[0])),
                "used_mb": int(float(parts[1])),
                "name": parts[2] if len(parts) > 2 else ""}
    except ValueError:
        return None


def _no_window():
    try:
        return subprocess.CREATE_NO_WINDOW
    except AttributeError:
        return 0


def _same_model(configured, reported):
    """Porovna jmeno modelu z config.json s tim, co hlasi server.

    Servery hlasi model ruzne: jednou jako 'google/gemma-3-12b-it-qat',
    jindy jako celou cestu k souboru .gguf. Lomitka se proto srovnaji.
    """
    a = configured.lower().replace("\\", "/").strip()
    b = reported.lower().replace("\\", "/").strip()
    if not a or not b:
        return False
    return a in b or b in a


def lm_studio(base_url, model_name, timeout=3.0):
    """Zjisti, jestli LM Studio bezi a jestli ma nacteny ocekavany model."""
    url = base_url.rstrip("/") + "/models"
    try:
        r = _CLIENT.get(url, timeout=timeout)
    except Exception as exc:
        return {
            "ok": False,
            "reason": "offline",
            "message": "LM Studio neodpovídá na " + url + ".",
            "hint": "Spusť LM Studio, otevři záložku Developer, zapni Start Server "
                    "na portu 1234 a načti model " + model_name + ".",
            "detail": type(exc).__name__,
            "models": [],
        }
    if r.status_code != 200:
        return {
            "ok": False,
            "reason": "http",
            "message": "LM Studio odpovědělo chybou " + str(r.status_code) + ".",
            "hint": "Zkontroluj v LM Studiu, že server běží na adrese z config.json.",
            "detail": r.text[:200],
            "models": [],
        }
    try:
        ids = [m.get("id", "") for m in r.json().get("data", [])]
    except Exception:
        ids = []
    loaded = any(_same_model(model_name, i) for i in ids)
    if not loaded:
        return {
            "ok": False,
            "reason": "model",
            "message": "LM Studio běží, ale model " + model_name + " není načtený.",
            "hint": "Načti v LM Studiu model " + model_name +
                    ", nebo uprav jméno modelu v config.json. Nabízí se: " +
                    (", ".join(ids) if ids else "nic"),
            "detail": "",
            "models": ids,
        }
    return {
        "ok": True,
        "reason": "",
        "message": "LM Studio běží, model " + model_name + " je načtený.",
        "hint": "",
        "detail": "",
        "models": ids,
    }
