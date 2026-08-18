"""Launch Hindsight with deterministic LLM calls for benchmark runs."""

import sys
from functools import wraps


def _with_evaluation_temperature(llm_method, temperature: float):
    @wraps(llm_method)
    async def method_with_evaluation_temperature(self, *args, **kwargs):
        if len(args) > 3:
            args = (*args[:3], temperature, *args[4:])
            kwargs.pop("temperature", None)
        else:
            kwargs["temperature"] = temperature
        return await llm_method(self, *args, **kwargs)

    return method_with_evaluation_temperature


def _configure_evaluation_temperature(
    llm_provider_class: type,
    openai_compatible_class: type,
    temperature: float,
) -> None:
    original_call = llm_provider_class.call
    original_tool_call = llm_provider_class.call_with_tools
    original_reasoning_check = openai_compatible_class._supports_reasoning_model

    @wraps(original_reasoning_check)
    def supports_reasoning_model(self) -> bool:
        if "deepseek" in self.model.lower():
            return False
        return original_reasoning_check(self)

    llm_provider_class.call = _with_evaluation_temperature(
        original_call,
        temperature,
    )
    llm_provider_class.call_with_tools = _with_evaluation_temperature(
        original_tool_call,
        temperature,
    )
    openai_compatible_class._supports_reasoning_model = supports_reasoning_model


def main() -> None:
    temperature = float(sys.argv.pop(1))

    from hindsight_api.engine.llm_wrapper import LLMProvider
    from hindsight_api.engine.providers.openai_compatible_llm import (
        OpenAICompatibleLLM,
    )
    from hindsight_api.main import main as hindsight_main

    _configure_evaluation_temperature(
        LLMProvider,
        OpenAICompatibleLLM,
        temperature,
    )
    hindsight_main()


if __name__ == "__main__":
    main()
