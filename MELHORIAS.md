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

## Próximo
1. Fontes do chat: a busca por palavra às vezes traz seções sem relação (ex.: "Boleto" na pergunta sobre Portugal). Cortar resultados com pontuação baixa ou trocar por embeddings.
2. Colocar as capturas no README.
3. Streaming de resposta (o chat da demo vai precisar).
4. Medir custo também nas avaliações e mostrar nota + custo lado a lado.
5. GitHub Actions rodando testes e uma suíte de exemplo.
6. Publicar no PyPI; README com GIF; post explicando as decisões.

## Decisões pendentes (dele)
- Nome final (`django-llm-ledger` está livre no PyPI em 07/11/2026).
- Em qual conta do GitHub publicar e quando abrir o repositório.
- Ollama não está instalado na máquina; precisa para a demo local.
