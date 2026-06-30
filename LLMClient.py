from defender.decision.llm_client import LLMClient
c = LLMClient({'provider':'ollama','model_name':'gemma4:e4b','base_url':'http://127.0.0.1:11434','strict_json':True})
r = c.complete_json('You are a test.', 'Return: {\"ok\":true}')
print('provider:', r.provider, '| latency_ms:', r.latency_ms, '| response:', r.raw_text[:200])

