# ADR-004: Multi-LLM Provider Abstraction (Cloud & Local)

## Status
Accepted

## Date
2026-07-26

## Context
Engineers and teams use different LLM providers based on privacy, enterprise compliance, and cost constraints:
- **Cloud Providers**: OpenAI (GPT-4o, o3-mini) and Anthropic (Claude 3.7 Sonnet).
- **Enterprise Cloud**: Azure OpenAI Service.
- **Privacy & Air-gapped Environments**: Ollama (llama3.3, deepseek-r1, qwen2.5-coder).

Requirements:
- Seamlessly switch providers via configuration without code modifications.
- Standardize patch formatting and structured output across models.

## Decision
Implement a provider-agnostic LLM factory function that instantiates standard LangChain chat model drivers (`ChatOpenAI`, `ChatAnthropic`, `ChatOllama`) based on the configured `llm_provider` setting.

## Consequences
- Full privacy support for air-gapped enterprise environments running local Ollama servers.
- Seamless failover or model switching directly from the web configuration UI.
- Unified prompt template and system message formatting across all supported model families.
