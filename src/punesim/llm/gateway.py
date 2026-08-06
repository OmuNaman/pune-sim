"""One door to every model (OpenRouter-first) with the cassette discipline and
the refusal ladder (08-identity §4).

Routing: call class -> model, with one override — identity_class >= 1 always
routes premium regardless of attention or budget (content-driven escalation).
Ladder per call: refusal detection (rung 2b: skip repair, reroute premium once,
then raise for defer-to-review) -> JSON extraction -> pydantic validation ->
one repair round -> SchemaError (caller falls back to rules). Refusals are
never silently templated. Every response is committed to the event log as
recorded nondeterminism when a log is attached.
"""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import blake2b
from typing import Any

import orjson
from pydantic import BaseModel, ValidationError

from ..config import Config
from ..kernel.log import EventLog
from .cassette import Cassette, CassetteMiss

Transport = Callable[..., tuple[str, dict]]  # (model, messages, temperature, max_tokens) -> (text, usage)

_REFUSAL_MARKERS = (
    "i can't",
    "i cannot",
    "i'm unable",
    "i am unable",
    "i won't be able",
    "as an ai",
    "i'm sorry, but",
    "cannot assist",
    "can't assist",
    "cannot help with",
    "against my guidelines",
    "i must decline",
    "i do not feel comfortable",
)

_CLASS_ROUTE = {
    "household_plan": "workhorse",
    "scene": "workhorse",
    "focal_turn": "premium",
    "micro_update": "flash",
    "qc_judge": "flash",
    "digest": "flash",
    "compiler": "workhorse",
}


class RefusalError(RuntimeError):
    """Both lanes refused. Caller defers to review — never silent templating
    for identity-salient content."""


class SchemaError(RuntimeError):
    """Output failed validation after one repair round."""


def detect_refusal(text: str) -> bool:
    """Rung 2b heuristics: refusal lexicon + no-JSON length anomaly."""
    t = text.strip().lower()
    if not t:
        return True
    if "{" in t:
        return False
    head = t[:250]
    return any(m in head for m in _REFUSAL_MARKERS) or len(t) < 80


def _extract_json(text: str) -> bytes:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in output")
    return text[start : end + 1].encode("utf-8")


@dataclass(frozen=True)
class LLMResult:
    parsed: Any  # validated schema instance, or raw text when schema is None
    raw: str
    model: str
    request_id: str
    usage: dict
    status: str  # ok | repaired | rerouted_premium | rerouted_repaired


class Gateway:
    def __init__(
        self,
        config: Config,
        cassette: Cassette,
        *,
        transport: Transport | None = None,
        log: EventLog | None = None,
    ):
        self.cfg = config
        self.cassette = cassette
        self.log = log
        self._transport = transport or self._openrouter_transport
        self._client = None

    # -- routing -----------------------------------------------------------

    def model_for(self, call_class: str, identity_class: int = 0) -> str:
        if identity_class >= 1:
            return self.cfg.model_premium
        lane = _CLASS_ROUTE.get(call_class, "workhorse")
        return {
            "workhorse": self.cfg.model_workhorse,
            "flash": self.cfg.model_flash,
            "premium": self.cfg.model_premium,
        }[lane]

    # -- plumbing ----------------------------------------------------------

    def _openrouter_transport(
        self, model: str, messages: list[dict], temperature: float, max_tokens: int
    ) -> tuple[str, dict]:
        if not self.cfg.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY missing — copy .env.example to .env and fill it")
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.cfg.openrouter_base_url, api_key=self.cfg.openrouter_api_key
            )
        resp = self._client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        usage = resp.usage.model_dump() if resp.usage else {}
        return resp.choices[0].message.content or "", usage

    @staticmethod
    def request_id(
        model: str, messages: list[dict], schema_name: str, temperature: float, max_tokens: int
    ) -> str:
        body = orjson.dumps(
            {
                "model": model,
                "messages": messages,
                "schema": schema_name,
                "t": temperature,
                "max": max_tokens,
            },
            option=orjson.OPT_SORT_KEYS,
        )
        return blake2b(body, digest_size=16).hexdigest()

    def _fetch(
        self, model: str, messages: list[dict], schema_name: str, temperature: float, max_tokens: int
    ) -> tuple[str, str, dict]:
        """Cassette-disciplined raw call. Returns (request_id, text, usage)."""
        rid = self.request_id(model, messages, schema_name, temperature, max_tokens)
        if self.cfg.llm_mode == "replay":
            rec = self.cassette.get(rid)
            if rec is None:
                raise CassetteMiss(f"replay miss for {rid} ({model})")
            return rid, rec.response, rec.usage
        rec = self.cassette.get(rid)
        if rec is not None:  # already recorded — reuse, keeps re-runs free
            return rid, rec.response, rec.usage
        text, usage = self._transport(model, messages, temperature, max_tokens)
        if self.cfg.llm_mode == "record":
            self.cassette.put(
                rid,
                model=model,
                request=orjson.dumps({"messages": messages}, option=orjson.OPT_SORT_KEYS),
                response=text,
                usage=usage,
            )
        return rid, text, usage

    # -- the call ----------------------------------------------------------

    def call(
        self,
        call_class: str,
        messages: list[dict],
        schema: type[BaseModel] | None = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 1200,
        identity_class: int = 0,
        sim_time: int = 0,
        model_override: str | None = None,
    ) -> LLMResult:
        schema_name = schema.__name__ if schema else "text"
        model = model_override or self.model_for(call_class, identity_class)
        status = "ok"

        rid, text, usage = self._fetch(model, messages, schema_name, temperature, max_tokens)

        if detect_refusal(text):
            if model != self.cfg.model_premium:
                model = self.cfg.model_premium  # rung 2b: skip repair, reroute once
                rid, text, usage = self._fetch(model, messages, schema_name, temperature, max_tokens)
                status = "rerouted_premium"
            if detect_refusal(text):
                raise RefusalError(f"refused on both lanes (class={call_class}, id_class={identity_class})")

        if self.log is not None:
            self.log.record_llm_response(
                request_id=rid, model=model, response_text=text, usage=usage, sim_time=sim_time
            )

        if schema is None:
            return LLMResult(text, text, model, rid, usage, status)

        try:
            parsed = schema.model_validate(orjson.loads(_extract_json(text)))
            return LLMResult(parsed, text, model, rid, usage, status)
        except (ValueError, ValidationError) as err:
            repair = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Your reply failed validation: {err}. "
                    "Reply again with ONLY a valid JSON object matching the schema.",
                },
            ]
            rid2, text2, usage2 = self._fetch(model, repair, schema_name, temperature, max_tokens)
            if self.log is not None:
                self.log.record_llm_response(
                    request_id=rid2, model=model, response_text=text2, usage=usage2, sim_time=sim_time
                )
            try:
                parsed = schema.model_validate(orjson.loads(_extract_json(text2)))
            except (ValueError, ValidationError) as err2:
                raise SchemaError(f"failed after repair: {err2}") from err2
            status = "rerouted_repaired" if status == "rerouted_premium" else "repaired"
            return LLMResult(parsed, text2, model, rid2, usage2, status)
