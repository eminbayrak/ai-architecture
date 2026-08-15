# Global agent memory

Keep this file short. Conditional knowledge belongs in a skill or a prompt under `agents/prompts/`.

## Writing

- Never use em-dashes in prose you write for this repo. Use a hyphen or restructure.
- Be direct. Lead with the outcome; supporting detail after.

## Technical decisions

- Prefer the higher-quality, more maintainable option. "Cheaper to build" is usually a false economy when an agent is writing the code.
- Do not add abstractions for cases that cannot happen.

## Tools

- Prefer CLIs over MCP servers where a good CLI exists.
- Skills under `skills/` are harness skills (Agent Skills format; current harness is Poolside). They are not LangGraph tools on day 1.
- Model ids belong in `agents/routing/catalog.yaml`, not in graph code.

## Workflow

- Do not claim work is done until `uv run pytest` passes.
- When planning non-trivial work, write a short plan that can be critiqued before coding.
