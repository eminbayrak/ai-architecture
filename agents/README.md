# Agents

Prompts in `prompts/` are the source of truth. `graphs/fde_crew.py` loads them as system messages. `routing/` is the architecture exhibit: role + complexity tier → model, no extra LLM call.

## FDE crew

| Node | Prompt | Writes | Default model (standard) |
|------|--------|--------|--------------------------|
| intake | `prompts/intake.md` | engagement brief | gpt-4.1-mini |
| research | `prompts/research.md` | research brief | gpt-4.1-mini |
| eval | `prompts/eval.md` | eval plan | gpt-4.1 |
| delivery | `prompts/delivery.md` | implementation plan + customer writeup | gpt-4.1 |

A node exception sets `error` and the graph stops. The CLI prints `route_log` so you can see which model ran.

```bash
uv run fde-crew "customer wants X under constraint Y"
```
