# awerouter: Routing That Costs Nothing to Decide

![awerouter](../../../logo/logo.png)

Most "smart" LLM routers are smart the expensive way. They run an LLM classifier over your request to decide where it should go. Or they match keywords against a curated list. Or they replay conversation history into a scoring function.

All of these share a hidden cost. The classifier burns tokens on every request — tokens you pay for, on top of the request itself. The keyword list adds latency and drifts out of date. The scoring function needs your conversation history, which means it is reading things it should not need to read.

And all of them guess. A classifier can be wrong in both directions. A wrong routing decision is either wasted money or degraded quality — invisible to you either way.

awerouter routes from structure. The shape of a request — what tools it declares, what model id it carries, how many tokens it holds, what the agent just did — is enough signal to route well. And reading that shape is free.

GitHub: [github.com/mugpeng/awerouter](https://github.com/mugpeng/awerouter)

## Four Questions, Zero Tokens

Every request goes through a first-match-wins pipeline. Four layers, each asking one question, each cheaper to answer than the last:

**Can flash even do this?** If the request declares a `web_search` tool, the question is capability, not difficulty. Many cheap providers do not support it. Route to a provider that can.

**What tier did the client pick?** Claude Code's `/model` picker already classifies tasks: background work versus think work. When the client sends `"flash"` or `"think"` as the model id, that is an instruction, not a signal to interpret. Route accordingly.

**How big is this?** Token count against a threshold. If the request carries more content than the dial allows — or contains an image — route to the strong tier. This is the only layer that estimates difficulty, and the estimate is a tape measure, not a classifier.

**Did code just change?** Structure cannot see the turn that decides an edit — by the time a tool call exists to read, the decision is already made. What it can do is react: the turn right after an edit is the review turn — verify the diff, decide the next file. That turn goes to the strong tier; the cheap one drafts, the strong one reviews. The most recent tool call is an honest signal, and it costs nothing to read.

If no layer fires, the default is the cheap tier. Innocent until proven expensive.

## The Signal Is Already in the Request

Here is the core insight: everything the router needs to decide is already present in the request body, before a single byte goes upstream.

- The `tools` array tells you what capabilities the request needs.
- The `model` field tells you what tier the client chose.
- The message content tells you how much context is in play.
- The last tool call tells you whether code just changed.

None of this requires interpretation. None of it requires a model call. The router reads the structure, makes a decision, and forwards the request — all in the time it takes to make one local HTTP round-trip.

## Not All Tokens Are Equally Hard

One detail worth knowing: the token count is not a raw sum. File-search results — grep matches, glob listings, directory trees — are bulk mechanical data. They inflate a request's size but add almost nothing to its reasoning difficulty.

A context stuffed with two hundred file paths is big, but it is not hard. If the router compared raw size, every search-heavy session would get promoted on the weight of its file listings alone — paying pro prices for an overgrown `ls` output.

So search-result tokens are counted at a discount before the threshold comparison. The router measures the portion of the context that actually stresses a model. Measure difficulty, not tonnage.

## The Router Does Not Read Your Conversations

The most unusual constraint in the design: the response path is opaque. The router never parses, buffers, or inspects what comes back from upstream. Response bytes stream from provider to client untouched.

This forecloses an entire category of "smarter" routers — ones that read the output, judge its quality, and adjust routing in a feedback loop. Without reading responses, there is no ground truth on quality, so there is no honest way to build a quality-sensitive router. awerouter does not pretend otherwise.

Instead, every decision comes from facts measurable before the first byte. Zero classification cost. The honesty is the design.

## Every Decision Has a Name

Every routed request gets a label: `webSearch`, `background`, `think`, `longContext`, `image`, `toolEdit`, `default` — plus `→fallback` when a cheap-tier failure was rescued.

When you look at your usage analytics and see 70% `default` and 14% `longContext`, you are not looking at a black box's verdicts. You are looking at exactly which question fired, for every request, with a record kept of each one.

A router that guessed would have to justify itself with vibes. A router that measures only has to show its work — and it does.

## The Bet, Restated

The structure of a request tells you enough about its cost and difficulty. Reading that structure is free. A router should never pay — in tokens, latency, or risk — to make a decision it can make for nothing.

Four questions, ordered from certainty to guesswork, with a cheap default and one-way safety doors. No classifier. No keywords. No oracle. Just measurement, honestly bounded.
