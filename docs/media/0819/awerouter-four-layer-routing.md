# awerouter: The Philosophy of a Four-Layer Router

![awerouter](../../../logo/logo.png)

Every routing tool answers the same question a thousand times a day: *this request — cheap model or strong model?*

Most tools answer it by trying to understand the request: an LLM classifier, a keyword list, a scoring function over the conversation. All of these spend tokens, add latency, and — worse — guess. A wrong routing decision is either wasted money or degraded quality, invisible to you either way.

awerouter makes the opposite bet: **route from structure, not semantics.** The shape of a request — what tools it declares, what model id it carries, how many tokens it holds, what the agent just did — is enough signal to route well, and it costs nothing to read.

GitHub: [github.com/mugpeng/awerouter](https://github.com/mugpeng/awerouter)

## Four Layers, Four Different Questions

The router is a first-match-wins pipeline. Each layer asks one question, and the first question that fires decides the route. The order is not arbitrary — each layer answers a progressively less certain kind of question.

**L1 asks: can flash even do this?** If the request declares a web-search tool, the question is not one of difficulty but of capability — many cheap providers simply do not support it. A capability constraint is absolute. It beats everything else, even a one-line query. You do not weigh whether a hammer or a scalpel is "better" for a job that one of them cannot perform.

**L2 asks: what did the client already decide?** Some clients — Claude Code's model picker is the clearest case — already classify their own tasks into tiers: background work versus think work. When the client says "this is a background task," that is not a signal to interpret. It is an instruction. Re-deriving what the client already told you would be paying twice for the same decision. The router trusts the client's own tier label and routes accordingly.

**L3 asks: how big is this, really?** Here the router measures instead of interpreting. If the request carries more content than a threshold — or contains an image, where cheap models degrade — it goes to the strong tier. This is the only layer where difficulty is *estimated*, and the estimate is deliberately crude: a token count against a dial. Not a classifier. A tape measure.

**L4 asks: did something just happen whose consequences deserve the strong model?** Structure cannot see the turn that *decides* an edit — by the time a tool call exists to read, the decision is already made. What structure can do is react. The turn right after code changed is the review turn: verify the diff, decide the next file, report to the user. That turn goes to pro; flash drafts, pro reviews. It is a consequence checkpoint, not a difficulty guess, and it is the weakest signal of the four — which is exactly why it sits at the bottom.

| Layer | Question | Certainty |
|---|---|---|
| L1 Capability | Can flash do this at all? | Hard constraint |
| L2 Intent | What tier did the client pick? | Client's explicit instruction |
| L3 Difficulty | How much content is this? | A measurement |
| L4 Consequence | Did code just change? | A reaction |

The hierarchy runs from absolute constraint down to educated guess. Hard constraints win over instructions; instructions win over measurements; measurements win over reactions. When two layers disagree, the more certain one decides. That is the whole ordering.

## Cost-First: Innocent Until Proven Expensive

The default route — the one a request takes when no layer fires — is the cheap tier.

This is a deliberate inversion. Most routing systems default to the strong model and demote "easy" requests. awerouter defaults to flash and promotes *justified* requests: needs a capability flash lacks, carries an explicit tier label, exceeds the difficulty threshold, or follows a fresh edit. The burden of proof sits on the request, not on your wallet.

The asymmetry makes sense because the failure modes are asymmetric. Sending a hard request to a weak model produces a bad answer — loud, visible, eventually noticed. Sending an easy request to a strong model produces a correct answer at triple the price — silent, invisible, compounding forever. The router biases toward the failure you can see, because the failure you cannot see is the one that costs you.

## Measure What Matters, Not What Is Big

The L3 token check has one subtlety worth its own section: not all tokens are equally hard.

File-search results — grep matches, glob listings, directory trees — are bulk mechanical data. They inflate a request's size dramatically but add almost nothing to its reasoning difficulty. A context stuffed with two hundred file paths is *big*, but it is not *hard*. If the router compared raw size, every search-heavy session would get promoted to pro on the weight of its file listings alone — paying pro prices for what is essentially an overgrown `ls` output.

So search-result tokens are counted at a discount before comparison against the threshold. The router measures the portion of the context that actually stresses a model: the conversation, the system prompt, the tool definitions, the reasoning payload. Bulk data rides along cheaply. Measure difficulty, not tonnage.

## One-Way Doors

Two places in the design, crossing a line is permanent — and permanence is the point.

**The long-context crossing only goes up.** Once a session's effective tokens exceed the threshold, it routes to pro and stays there, regardless of what tool runs next. Below the threshold, the edit checkpoint may still lift individual turns — the turn after an edit goes to pro, later turns return to flash. Above it, the session is committed. This keeps cheap models away from context sizes they may degrade on. A router that ping-ponnged a long session back to flash for one cheap tool call would save pennies while flirting with a capability cliff.

**Streaming never falls back.** If the cheap provider fails *before* the first byte reaches the client, the request silently retries once on the strong tier. If it fails *after* streaming has begun, no retry — bytes already sent cannot be unsent. The fallback itself follows the same philosophy as the routing: it only ever goes in the safe direction, cheap to strong, never the reverse. A strong provider's failure is terminal; there is nowhere left to escalate.

## The Router Does Not Read Your Conversations

Here is the most unusual constraint in the design: the response path is opaque. The router never parses, buffers, or inspects what comes back from upstream. Response bytes stream from provider to client untouched.

This forecloses an entire category of "smarter" routers — ones that read the output, judge its quality, and adjust routing in a feedback loop. awerouter cannot do that and chose not to fake it. Without reading responses, there is no ground truth on quality, so there is no honest way to build a quality-sensitive router. Pretending otherwise would mean a feedback loop built on guesses.

Instead, the router confines itself to the signals it can read honestly and cheaply: the request's structure. Every decision comes from facts measurable before the first byte — tool declarations, model ids, token counts, the last tool call. Zero classification cost. The honesty is the design: the tool does not pretend to know more than it can see.

## Every Decision Has a Name

Finally, the router labels every request it resolves: `webSearch`, `background`, `think`, `longContext`, `image`, `toolEdit`, `default` — and `→fallback` when a cheap-tier failure was rescued.

This is not decoration. A structural router earns trust the same way it makes decisions: by being explainable. When you look at your usage analytics and see 70% `default` and 14% `longContext`, you are not looking at a black box's verdicts. You are looking at exactly which question fired, for every request, with a record kept of each one. Any routing decision can be replayed from its label. Any threshold can be re-tuned from the logged distribution.

A router that guessed would have to justify itself with vibes. A router that measures only has to show its work — and it does.

## The Bet, Restated

Strip away the layers and the philosophy is one sentence: **the structure of a request tells you enough about its cost and difficulty, reading that structure is free, and a router should never pay — in tokens, latency, or risk — to make a decision it can make for nothing.**

Four questions, ordered from certainty to guesswork, with a cheap default and one-way safety doors. No classifier. No keywords. No oracle. Just measurement, honestly bounded.
