import unittest
from types import SimpleNamespace

from memory_bench.llm.base import Schema
from memory_bench.llm.gemini import GeminiLLM
from memory_bench.llm.groq import GroqLLM
from memory_bench.llm.openai import OpenAILLM


_SCHEMA = Schema(
    properties={"answer": {"type": "string"}},
    required=["answer"],
)


class RecordingCompletions:
    def __init__(self):
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer": "ok"}'))]
        )


class RecordingGeminiModels:
    def __init__(self):
        self.request = None

    def generate_content(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(parsed={"answer": "ok"})


class LlmTemperatureTest(unittest.TestCase):
    def test_chat_completion_providers_use_zero_temperature(self):
        for llm_class in (OpenAILLM, GroqLLM):
            completions = RecordingCompletions()
            llm = llm_class.__new__(llm_class)
            llm._client = SimpleNamespace(
                chat=SimpleNamespace(completions=completions)
            )
            llm._model = "test-model"

            with self.subTest(provider=llm_class.__name__):
                llm.generate("test prompt", _SCHEMA)

                self.assertEqual(completions.request["temperature"], 0.0)

    def test_gemini_uses_zero_temperature(self):
        models = RecordingGeminiModels()
        llm = GeminiLLM.__new__(GeminiLLM)
        llm._client = SimpleNamespace(models=models)
        llm._model = "test-model"

        llm.generate("test prompt", _SCHEMA)

        self.assertEqual(models.request["config"].temperature, 0.0)


if __name__ == "__main__":
    unittest.main()
