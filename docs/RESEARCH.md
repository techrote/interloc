# Research register and feasibility gates

Checked 2026-09-12. These are narrowly scoped findings, not certification of a future implementation. Links are primary documentation or directly inspected repository material. Re-check mutable product documentation during implementation. Quoted snippets are intentionally avoided; findings are paraphrased.

## Source register

| Source | Reference | Finding used |
|---|---|---|
| S1 | https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan | Codex CLI is a Codex client; current help describes shared Codex/Work allowance. This is not an assurance about every future Chat feature's metering. |
| S2 | https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt | Standard-chat GitHub availability varies; live access is not a synced index. Its read-only description differs from write tools exposed in this session. Probe actual capabilities. |
| S3 | https://openai.com/policies/eu-terms-of-use/ | Applicable page includes UK users and restrictions on automated output/data extraction. Automatic reply scraping is not approved by this plan. A support/permission or documented integration gate is needed; this is a product-risk decision, not legal advice. |
| S4 | https://docs.github.com/en/rest/repos/contents | Contents API is ref-addressable; larger files have restrictions; expiring download URLs must not become durable pointers. Concurrent contents updates/deletes need serialization. |
| S5 | https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api | Honor rate limits, retry guidance, conditional requests and serialized writes; do not build an unbounded poll/write loop. |
| S6 | https://cli.github.com/manual/gh_api | gh supports explicit endpoints, HTTP methods, input, headers and pagination. Interloc must wrap fixed operations, not accept arbitrary gh argument arrays. |
| S7 | https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture | Windows provides window/display capture APIs with capability and consent considerations. This does not prove capture of minimized, protected, elevated or GPU windows in the chosen host. |
| S8 | https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session | A pseudoconsole has lifecycle/pipe/session requirements. A log tail is not equivalent to attaching to any existing terminal/TUI. |
| S9 | https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging | A browser extension can communicate with an explicitly registered native host. This is only an IPC mechanism, not ChatGPT automation permission. |
| S10 | https://help.openai.com/en/articles/20001276 | Current desktop migration guidance exists. It does not establish a supported normal-Chat terminal API or guarantee a lighter UI. No Classic-client dependency is selected. |
| S11 | https://github.com/techrote/ansible/blob/main/POLICY.md | Live policy: trusted human-initiated execution; intrallm is reference/data; executable updates are explicit local actions. |
| S12 | https://github.com/techrote/ansible/issues/1 | Live open task: fixed launchers/runners, isolated state and status TUI; integration cannot assume those executables are already shipped. |
| S13 | Exposed GitHub tool contracts in this planning session | `fetch_file` supports ref/line-bound text; `fetch` rejects binary; attachment download accepts private-user-images URLs only. `create_file` successfully initialized interloc. These are session observations, not portable API promises. |

## Evidence ledger

Observed: repository was private and empty, with zero issues/PRs before initialization; text write succeeded. No local machine commands, capture, gh authentication or Chat usage-accounting experiment was performed.

Not found in the documentation inspected: a supported ordinary consumer-Chat CLI or an API to append to an existing consumer conversation. This is a bounded search finding, not proof that no third-party solution exists.

The model can receive tool results during an active turn. A GitHub update alone does not create a new ordinary-Chat user turn. Provider webhook tasks are separate features and must not be used as an unexamined workaround for R1.

## Gate cards

**G-chat / IL-011.** Record date, app/browser/build, displayed mode, exposed read/write tools, explicit private text write/read-by-full-SHA test, and whether a mode transition occurred. Observe metering only through legitimately available UI; no inference from inaccessible counters. If write is unavailable, manually import a strict JSON request through `interloc request import`. If read is unavailable in ordinary Chat, manual attachment is the fallback; mark repo-retrieval integration unavailable, not passed.

**G-capture / IL-012.** Run a real Windows matrix for a synthetic fixture window, Windows Terminal and a GPU-rendered test window, with visible/occluded/minimized states, 100/150/200% DPI, negative monitor coordinates, lock and access-denied cases. Choose backend only from retained results. No desktop fallback when scoped capture fails.

**G-vision / IL-014.** Distinguish capture success, artifact storage, authenticated retrieval and model vision delivery. A synthetic image with known shapes/text must actually be available as an image input. Text/base64 or a successful repo link is not evidence of vision. Default accepted outcome: explicit local preview + manual Chat attachment with matching artifact ID/hash. Unsupported automatic delivery is recorded as unavailable. Never publish publicly to make this test pass.

**G-browser / IL-020.** Evaluate documented APIs, permission/terms, input assistance, mode detection and provider support. No private endpoints/auth-token scraping. The baseline completion may be a negative decision retaining manual handoff. Automatic response scraping is excluded absent an explicitly supported/permitted route. An extension's ability to manipulate DOM is not evidence of authorization, reliable mode isolation or reduced backend latency.

**G-assisted / IL-022.** Conditional implementation only after a positive G-browser route-specific decision and G-chat evidence. Otherwise hold this issue with reason and continue core release. A future official transport can implement the notifier interface without redesigning the broker.

## Unresolved questions

The user's exact Windows capture backend, runtime mailbox identity/permissions, availability of connector writes in their chosen ordinary-Chat surface, and a supported unattended Chat/vision transport all need runtime evidence. None blocks offline schemas, policy, broker, log capture or manual handoff development. Do not claim reduced Chat inference latency: Interloc can reduce copying and rendering volume, not fix the provider's backend.
