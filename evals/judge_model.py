"""El juez de DeepEval pasa por ``llm_client`` (AGENTS.md: un solo sitio llama al modelo).

``EVAL_JUDGE_MODEL`` elige el modelo; conviene que sea de otra familia que el generador
para reducir el sesgo de favorecer lo suyo (fase 4). Vacío = ``LLM_MODEL``.
"""

from __future__ import annotations

import asyncio
import functools
import os

from deepeval.models import DeepEvalBaseLLM

from app.config import get_settings
from app.services import llm_client


class EstimatorJudge(DeepEvalBaseLLM):
    def __init__(self, model: str | None = None) -> None:
        self.model_name = model or os.environ.get("EVAL_JUDGE_MODEL") or get_settings().LLM_MODEL
        self._loop: asyncio.AbstractEventLoop | None = None
        super().__init__(self.model_name)

    def load_model(self, *args, **kwargs):
        return self

    def get_model_name(self, *args, **kwargs) -> str:
        return f"estimador:{self.model_name}"

    async def a_generate(self, prompt: str, schema=None, **kwargs):
        """DeepEval evalúa en su propio bucle. Con app viva, la llamada se hace en el bucle de la
        app (portal) desde un hilo, para no bloquear el bucle de DeepEval ni mezclar bucles."""
        if self.portal is not None:
            call = functools.partial(self._complete, prompt, schema)
            return await asyncio.to_thread(self.portal.call, call)
        return await self._complete(prompt, schema)

    async def _complete(self, prompt: str, schema=None):
        response_format = {"type": "json_object"} if schema is not None else {"type": "text"}
        result = await llm_client.complete(
            [{"role": "user", "content": prompt}],
            response_format,
            name="eval-judge",
            model=self.model_name,
        )
        if schema is None:
            return result.content
        return schema.model_validate_json(result.content)

    # Con la app viva (suite evals/), el portal del TestClient: el juez corre en el mismo bucle
    # de asyncio que la app y usa su mismo cliente HTTP. Sin app (calibración), bucle propio.
    portal = None

    def generate(self, prompt: str, schema=None, **kwargs):
        """Versión síncrona (GEval con ``async_mode=False``).

        El cliente HTTP de ``llm_client`` y otros objetos de asyncio quedan atados al bucle donde
        se usaron por primera vez; mezclar bucles rompe. Por eso, con portal, el juez se ejecuta
        en el bucle de la app; sin él, en un bucle propio persistente con un cliente nuevo.
        """
        if self.portal is not None:
            return self.portal.call(functools.partial(self._complete, prompt, schema))
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        llm_client.reset_client()
        try:
            return self._loop.run_until_complete(self._complete(prompt, schema))
        finally:
            llm_client.reset_client()
