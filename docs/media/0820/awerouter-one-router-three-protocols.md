# awerouter: One Router, Three Protocols, Any Provider Mix

![awerouter](../../../logo/logo.png)

Here is something most routing tools will not tell you up front: they are locked to one wire protocol and one provider ecosystem. An Anthropic-only router cannot speak to a GLM endpoint. An OpenAI-only router cannot front Claude Code. If you want to mix StepFun for cheap work and Anthropic for hard work, you need two tools.

awerouter does not have that limitation. It speaks three protocols natively — Anthropic Messages, OpenAI Chat Completions, OpenAI Responses — and within a single routing profile you can mix as many providers as you like across those protocols. The router does not care what is on the other end. It only cares which end is cheap and which one is strong.

GitHub: [github.com/mugpeng/awerouter](https://github.com/mugpeng/awerouter)

## One Profile, Many Providers

A routing profile has two destinations: `flash` and `pro`. Each destination is a comma-separated string of `providerName,modelId`. The provider name maps to an entry in `providers.json`, which stores the endpoint and auth for the matching protocol group.

```json
{
  "cc-router-1": {
    "protocol": "anthropic",
    "longContextThreshold": 8000,
    "destinations": {
      "flash": "stepfun,step-3.7-flash",
      "pro":   "anthropic,claude-opus-5"
    }
  }
}
```

The same profile can just as easily point `flash` at GLM and `pro` at OpenAI. The protocol group in `providers.json` carries the matching `base_url` — and because each provider often uses a different path per protocol, the config lets you specify the right endpoint for each wire format:

| Protocol | Base URL convention | Endpoint |
|---|---|---|
| `anthropic` | `ANTHROPIC_BASE_URL` style, no `/v1` | `base_url + /v1/messages` |
| `openai-chat` | `OPENAI_BASE_URL` includes version | `base_url + /chat/completions` |
| `openai-responses` | `OPENAI_BASE_URL` includes version | `base_url + /responses` |

GLM, for instance, uses `https://open.bigmodel.cn/api/coding/paas/v4` for chat completions but `https://open.bigmodel.cn/api/v1` for responses. awerouter stores both under the same provider name, each in its own protocol group, and picks the right one at request time.

## The Client Does Not Need to Know

From the client's perspective, nothing changed. Claude Code still points at `ANTHROPIC_BASE_URL=http://127.0.0.1:20128`. Codex still talks to `OPENAI_BASE_URL`. OpenCode uses its own `OPENCODE_BASE_URL`. The awerouter daemon terminates the native protocol, applies the routing decision, and forwards the request upstream in the same wire format.

This means you can run Claude Code, Codex, and OpenCode against the same daemon on the same machine, each through its own routing profile, each mixing providers differently. The router is the common layer. The clients never see each other.

## Protocol-Agnostic Routing

The routing decision itself is completely protocol-blind. The `resolve()` function receives a precomputed `InspectResult` — a normalized snapshot of the request's structure — and returns a `ResolveResult` with a destination and a label. It does not know whether the incoming request was Anthropic Messages or OpenAI Chat Completions. It does not need to.

Three protocol-specific extractors produce the same `InspectResult`:

- `token_count` — estimated total input tokens
- `has_image` — any image block present
- `has_web_search` — `web_search_*` tool declared
- `file_search_tokens` — tokens from grep/glob/ls results only
- `last_tool` — most recent tool call name

One router. Three extractors. Same decision.

## Mixing Providers Is Not an Afterthought

The reason multi-provider routing matters is not flexibility for its own sake. It is the price and capability spread between providers.

A typical setup: StepFun `step-3.7-flash` handles the high-frequency routine traffic at a fraction of the cost. Anthropic `claude-opus-5` handles the genuinely hard sessions. If you also use GLM for certain coding tasks, it slots in alongside them — no second router, no proxy chain, no client reconfiguration.

Adding a provider is a config edit, not an architecture change. The agent can do it in one turn:

> "Add GLM as a provider in the openai-chat group. Set flash to `glm,glm-4-flash`."

## Why This Matters

The first generation of LLM proxies assumed a one-to-one relationship: one client, one provider, one protocol. That model breaks down the moment you actually want to mix.

awerouter's design treats the protocol layer as transport and the provider mix as strategy. They are independent axes. You can change providers without touching routing logic. You can change routing logic without touching providers. The four-layer decision pipeline does not care which provider sits behind `flash` or `pro` — it only cares that `flash` exists and `pro` exists.

That separation is what lets one router, one daemon, one config directory serve every agent on your machine, across every provider you use, in every protocol they speak.
