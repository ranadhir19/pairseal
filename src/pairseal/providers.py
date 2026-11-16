"""Vision-model readers: pluggable providers behind one interface.

A reader takes image bytes and returns the model's raw reply text;
parsing and agreement live elsewhere (pairseal.reads), so a provider
can be swapped without touching the gate.

Credential discipline: keys are NEVER read from files, arguments, or
this module's source, and never printed. Resolution order per
provider: (1) the Muse connector credential surrogate, when the
dynamic_credentials helper is importable (this is how the stored
free-tier keys are used inside Muse); (2) the provider's standard
environment variable (GEMINI_API_KEY / GOOGLE_AI_STUDIO_API_KEY /
GROQ_API_KEY) for standalone installs. A run that can resolve no
credential fails that reader with a `no_credentials` provider error
— recorded in the corpus, never silently skipped.

Rate limits: an HTTP 429 raises RateLimited. The gate treats it as
a hard stop for that provider in that run: recorded, never retried
in a loop. Callers who want a later attempt start a later run.

Transports:
* GeminiReader — Google AI Studio, Interactions API
  (POST /v1beta/interactions). The classic :generateContent route
  404s for some accounts; the Interactions API is the verified one.
  The response's own `model` field is recorded as model_reported,
  because the API may alias the requested id.
* GroqReader — Groq's OpenAI-compatible chat completions with an
  image_url data URI. NOTE (measured 2026-11-15): the stored
  free-tier Groq key's model list carries NO vision model, so this
  reader currently fails with model_not_found on that key; it is
  here, tested, for keys and catalogues that do carry one.
* OCRReader — classical OCR baseline via a local `tesseract`
  binary when present. Its "read" is a heuristic line parse and is
  labelled as such; it exists so the corpus can score the classical
  channel against the vision channels. OCR never gates a price on
  its own in the recommended configuration — the production lesson
  that produced this tool is that simple OCR garbles flyer digits.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

from .reads import extraction_prompt

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36"}
_CONNECTOR_BIN = "/opt/muse/skills/custom-connector-setup/bin"


class ProviderError(Exception):
    """A provider call failed. `code` is the stable machine code
    recorded in the corpus (e.g. http_429, model_not_found,
    no_credentials); `status` is the HTTP status when there is one."""

    def __init__(self, code: str, status: int | None = None,
                 message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.status = status


class RateLimited(ProviderError):
    def __init__(self, status: int = 429, message: str = ""):
        super().__init__("http_429", status, message or
                         "rate limited (hard stop for this run)")


def _connector():
    """The dynamic_credentials module, or None when this install is
    not running inside Muse's connector environment."""
    if _CONNECTOR_BIN not in sys.path:
        sys.path.insert(0, _CONNECTOR_BIN)
    try:
        import dynamic_credentials
        return dynamic_credentials
    except ImportError:
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused")


def _post_json(url: str, payload: dict, connector_name: str | None,
               host: str, env_key_names: tuple[str, ...],
               key_header: dict | None, timeout: int = 120) -> dict:
    """One authenticated POST. Auth comes from the connector
    surrogate when available, else from an env-var key placed in
    `key_header` (a {header_name: value_template} with {key})."""
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Accept": "application/json", **_UA}
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST")
    dc = _connector() if connector_name else None
    if dc is not None:
        dc.add_surrogate_to_request(req, connector_name,
                                    allowed_hosts=[host])
    else:
        key = next((os.environ[n] for n in env_key_names
                    if os.environ.get(n)), None)
        if not key or key_header is None:
            raise ProviderError(
                "no_credentials", None,
                "no connector surrogate and none of "
                + "/".join(env_key_names) + " is set")
        for name, template in key_header.items():
            req.add_header(name, template.format(key=key))
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            if dc is not None:
                return dc.read_json_response(resp)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001
            pass
        if e.code == 429:
            raise RateLimited(429, body) from e
        code = "http_%d" % e.code
        if "model_not_found" in body or "does not exist" in body:
            code = "model_not_found"
        raise ProviderError(code, e.code, body) from e
    except urllib.error.URLError as e:
        raise ProviderError("network_error", None,
                            str(e.reason)) from e


def _family_of(model: str) -> str:
    m = model.lower()
    for fam in ("gemini", "gemma", "llama", "qwen", "mistral",
                "phi", "deepseek"):
        if fam in m:
            return fam
    return m.split("/")[-1].split("-")[0]


class GeminiReader:
    """Google AI Studio vision reader (Interactions API)."""

    provider = "google"
    connector_name = "custom.google-ai-studio"
    host = "generativelanguage.googleapis.com"
    base = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, model: str = "gemini-3.5-flash"):
        self.model = model
        self._reported: str | None = None

    def identity(self) -> dict:
        return {"provider": self.provider,
                "family": _family_of(self.model),
                "model_requested": self.model,
                "model_reported": self._reported}

    def read(self, image_bytes: bytes, media_type: str,
             doc_type: str = "auto",
             generic_fields: list[str] | None = None) -> dict:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": "models/" + self.model,
            "input": [
                {"type": "text",
                 "text": extraction_prompt(doc_type, generic_fields)},
                {"type": "image", "data": b64,
                 "mime_type": media_type},
            ],
        }
        result = _post_json(self.base + "/interactions", payload,
                            self.connector_name, self.host,
                            ("GEMINI_API_KEY", "GOOGLE_AI_STUDIO_API_KEY"),
                            {"x-goog-api-key": "{key}"})
        self._reported = result.get("model") or self.model
        text = ""
        for step in result.get("steps") or []:
            if step.get("type") == "model_output":
                for part in step.get("content") or []:
                    if part.get("type") == "text":
                        text += part.get("text", "")
        return {"raw_text": text, "model_reported": self._reported,
                "usage": result.get("usage")}


class GroqReader:
    """Groq vision reader (OpenAI-compatible chat completions)."""

    provider = "groq"
    connector_name = "custom.groq"
    host = "api.groq.com"
    base = "https://api.groq.com/openai/v1"

    def __init__(self,
                 model: str = "meta-llama/llama-4-scout-17b-16e-instruct"):
        self.model = model
        self._reported: str | None = None

    def identity(self) -> dict:
        return {"provider": self.provider,
                "family": _family_of(self.model),
                "model_requested": self.model,
                "model_reported": self._reported}

    def read(self, image_bytes: bytes, media_type: str,
             doc_type: str = "auto",
             generic_fields: list[str] | None = None) -> dict:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 4096,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": extraction_prompt(doc_type,
                                               generic_fields)},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ],
            }],
        }
        result = _post_json(self.base + "/chat/completions", payload,
                            self.connector_name, self.host,
                            ("GROQ_API_KEY",),
                            {"Authorization": "Bearer {key}"})
        self._reported = result.get("model") or self.model
        text = ""
        for choice in result.get("choices") or []:
            msg = choice.get("message") or {}
            if msg.get("content"):
                text += msg["content"]
        return {"raw_text": text, "model_reported": self._reported,
                "usage": result.get("usage")}


class OCRReader:
    """Classical OCR baseline (local tesseract, when installed).

    The parse is deliberately simple — lines pairing a product-ish
    line with a price-looking token — and the read is labelled
    method=heuristic_line_parse wherever it is recorded. This
    channel exists as a measured baseline, not as a gate."""

    provider = "local-ocr"
    model = "tesseract"

    def __init__(self):
        self._reported = None

    def identity(self) -> dict:
        return {"provider": self.provider, "family": "ocr",
                "model_requested": self.model,
                "model_reported": self._reported}

    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def read(self, image_bytes: bytes, media_type: str,
             doc_type: str = "auto",
             generic_fields: list[str] | None = None) -> dict:
        if not self.available():
            raise ProviderError("ocr_unavailable", None,
                                "no tesseract binary on PATH")
        suffix = ".png" if media_type == "image/png" else ".jpg"
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=suffix,
                                         delete=False) as fh:
            fh.write(image_bytes)
            path = fh.name
        try:
            out = subprocess.run(["tesseract", path, "stdout"],
                                 capture_output=True, text=True,
                                 timeout=120)
        finally:
            os.unlink(path)
        if out.returncode != 0:
            raise ProviderError("ocr_failed", None,
                                out.stderr[:200])
        self._reported = "tesseract"
        return {"raw_text": _ocr_text_to_read_json(out.stdout),
                "model_reported": self._reported,
                "usage": None,
                "method": "heuristic_line_parse"}


_PRICE_TOKEN = re.compile(r"\$?\d+(?:[.,]\d{1,2})?")


def _ocr_text_to_read_json(text: str) -> str:
    """Heuristic: a line containing a price token is an item whose
    name is the line with the token removed. Weak by design — this
    is the baseline the vision channels are measured against.
    Emits the flyer item shape; OCR is a flyer/receipt baseline."""
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _PRICE_TOKEN.search(line)
        if not m:
            continue
        name = (line[:m.start()] + line[m.end():]).strip(" -:")
        if len(name) < 3:
            continue
        items.append({"name": name, "value": None,
                      "value_text": m.group(0), "unit": None,
                      "qty": None})
    return json.dumps({"doc_type": "flyer",
                       "unreadable": not items,
                       "validity_text": None, "fields": {},
                       "items": items})


def make_reader(spec: str):
    """'gemini:<model>' | 'groq:<model>' | 'ocr' -> a reader."""
    if spec == "ocr":
        return OCRReader()
    kind, _, model = spec.partition(":")
    if kind == "gemini" and model:
        return GeminiReader(model)
    if kind == "groq" and model:
        return GroqReader(model)
    raise ValueError(f"unknown reader spec: {spec!r} "
                     "(want gemini:<model>, groq:<model>, or ocr)")


DEFAULT_PAIR = ("gemini:gemini-3.5-flash", "gemini:gemma-4-31b-it")
