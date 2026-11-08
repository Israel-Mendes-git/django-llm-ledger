# Próximos passos e decisões — django-llm-ledger

## Pronto (07/11/2026)
- Chamada `llm.complete()` com registro de cada tentativa (custo, tokens, latência, status, feature, usuário).
- Provedores sem SDK: Ollama, compatíveis com OpenAI, Anthropic, e um falso para testes/demo.
- Repetição com espera exponencial e plano B pela cadeia de provedores.
- Cache de respostas iguais.
- Orçamento mensal com sinal e bloqueio opcional.
- Prova de qualidade (`manage.py llm_evaluate`) em JSON/YAML, com função-alvo opcional.
- Painel de custos no admin.
- 37 testes passando.

- Loja de demonstração em `example/` (Serra Verde Coffee): chat com fontes, painel de custo ao lado, simulação de queda do provedor. Prova de qualidade 10/10 com `qwen2.5:1.5b` local (07/11/2026). Capturas em `..\django-llm-ledger-shots\`.

- Avaliações com custo, tokens e tempo por caso; painel mostra nota e custo lado a lado (08/11/2026).
- GitHub Actions: testes em Python 3.10–3.13 com Django 4.2–6.1 e checagem de migração faltando (08/11/2026).

- Streaming: `llm.stream()` nos 3 provedores, plano B só antes da primeira palavra; chat da loja escreve a resposta aos poucos (08/11/2026).

## Próximo
1. Busca da loja: corte de fontes fracas feito (15 de 16 perguntas trazem só a seção certa; "Japão" ainda cita "Boleto"). Próximo nível é embeddings.
2. Publicar no PyPI; README com GIF; post explicando as decisões.

## Decisões pendentes (dele)
- Nome final (`django-llm-ledger` está livre no PyPI em 07/11/2026).
- Quando abrir o repositório (hoje privado em Israel-Mendes-git).
