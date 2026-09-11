"""Rozhrani k LM Studiu pres HTTP. Vlastni inferencni vrstva se tu nepise.

Adresa, jmeno modelu i parametry vzorkovani jsou v config.json. Model bez
chatu (TranslateGemma) dostava hotovy text pres /v1/completions.
"""
import json

import httpx

from . import config
from .config import CFG


class LLMError(Exception):
    """Chyba na strane LM Studia nebo spojeni s nim."""


# trust_env=False: pripadna promenna HTTP_PROXY by preklad poslala pres proxy,
# tedy ven z pocitace. Spojeni musi zustat mistni.
_CLIENT = httpx.Client(trust_env=False)

SAMPLING = ("temperature", "top_p", "repeat_penalty", "seed", "max_tokens")


def _url(endpoint="chat/completions"):
    return CFG["lm_studio"]["base_url"].rstrip("/") + "/" + endpoint


def _timeout():
    return httpx.Timeout(connect=5.0, read=float(CFG["lm_studio"]["timeout_s"]),
                         write=30.0, pool=5.0)


def _params(model):
    """Vzorkovani z "inference", prepsane nastavenim konkretniho modelu."""
    inf = dict(CFG["inference"])
    for key, value in config.profile(model).items():
        if key in SAMPLING:
            inf[key] = value
    return inf


def _payload(messages, stream, with_repeat_penalty=True, model=None):
    model = model or CFG["lm_studio"]["model"]
    inf = _params(model)
    body = {
        "model": model,
        "messages": messages,
        "temperature": inf["temperature"],
        "top_p": inf["top_p"],
        "seed": inf["seed"],
        "max_tokens": inf["max_tokens"],
        "stream": stream,
    }
    if with_repeat_penalty:
        # llama.cpp nazev, LM Studio ho bere; kdyby ne, posle se dotaz bez nej
        body["repeat_penalty"] = inf["repeat_penalty"]
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


def _completion_payload(prompt, stream, with_repeat_penalty=True, model=None, stop=None):
    body = _payload([], stream, with_repeat_penalty, model)
    del body["messages"]
    body["prompt"] = prompt
    if stop:
        body["stop"] = list(stop)
    return body


def _explain(exc):
    if isinstance(exc, httpx.ConnectError):
        return ("LM Studio neodpovídá na " + CFG["lm_studio"]["base_url"] +
                ". Spusť ho, v nastavení zapni Local Models → Local Model API"
                " → Local API server a načti model "
                + CFG["lm_studio"]["model"] +
                ". Ve starší řadě 0.3 je totéž pod záložkou Developer.")
    if isinstance(exc, httpx.ReadTimeout):
        return ("LM Studio neodpovědělo do " + str(CFG["lm_studio"]["timeout_s"]) +
                " s. Model nejspíš počítá příliš dlouho, nebo se zasekl.")
    return str(exc)


def stream_chat(messages, should_stop=None, model=None):
    """Posle dotaz a vraci kousky odpovedi, jak prichazeji.

    Vydava dvojice ("delta", text), ("finish", duvod ukonceni) a nakonec
    ("usage", slovnik) s poctem tokenu, pokud ho server posle. Kdyz
    should_stop() vrati True, spojeni se zavre.
    """
    yield from _stream(
        lambda rp: ("chat/completions", _payload(messages, True, rp, model)),
        should_stop)


def stream_completion(prompt, should_stop=None, model=None, stop=None):
    """Hotovy text bez chatove sablony. Vydava stejne dvojice jako stream_chat."""
    yield from _stream(
        lambda rp: ("completions", _completion_payload(prompt, True, rp, model, stop)),
        should_stop)


def _stream(build, should_stop):
    for attempt, with_rp in enumerate((True, False)):
        endpoint, body = build(with_rp)
        try:
            yield from _stream_once(endpoint, body, should_stop)
            return
        except httpx.HTTPStatusError as exc:
            # nekterym serverum vadi repeat_penalty, zkusi se dotaz bez nej
            if attempt == 0 and exc.response.status_code == 400:
                continue
            raise LLMError("LM Studio odpovědělo chybou " +
                           str(exc.response.status_code) + ": " +
                           exc.response.text[:300])
        except httpx.HTTPError as exc:
            raise LLMError(_explain(exc))


def _stream_once(endpoint, body, should_stop):
    with _CLIENT.stream("POST", _url(endpoint), json=body, timeout=_timeout()) as res:
        if res.status_code != 200:
            res.read()
            res.raise_for_status()
        for line in res.iter_lines():
            if should_stop is not None and should_stop():
                return
            if not line or not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                return
            try:
                obj = json.loads(chunk)
            except ValueError:
                continue
            for choice in obj.get("choices") or []:
                # /completions posila "text", chat posila "delta.content"
                piece = choice.get("text")
                if piece is None:
                    piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield "delta", piece
                if choice.get("finish_reason"):
                    yield "finish", choice["finish_reason"]
            usage = obj.get("usage")
            if usage:
                yield "usage", usage


def chat(messages):
    """Nestreamovana varianta. Vraci cely text odpovedi."""
    try:
        res = _CLIENT.post(_url(), json=_payload(messages, False), timeout=_timeout())
        if res.status_code == 400:
            res = _CLIENT.post(_url(), json=_payload(messages, False, False),
                               timeout=_timeout())
        res.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LLMError("LM Studio odpovědělo chybou " +
                       str(exc.response.status_code) + ": " +
                       exc.response.text[:300])
    except httpx.HTTPError as exc:
        raise LLMError(_explain(exc))
    data = res.json()
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("LM Studio vrátilo prázdnou odpověď.")
    return (choices[0].get("message") or {}).get("content") or ""
