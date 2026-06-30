from defender.decision.llm_client import LLMClient


def test_ollama_keep_alive_configuration_is_supported():
    client = LLMClient({"provider": "ollama", "ollama_keep_alive": "45m"})
    assert client.keep_alive == "45m"
