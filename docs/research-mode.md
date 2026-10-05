# Research mode

`/research <question>` (or the model calling `deep_research` on its own) answers a question that needs many searches, and shows how it got there.

## How it runs

1. **Plan.** One model call splits the question into sub-questions: up to 3 for `normal`, up to 6 for `deep`.
2. **Fan out.** Each sub-question goes to its own read-only researcher, an ordinary subagent limited to `web_search`, `fetch_url`, `read_feed`, `youtube_video` and `github_read`. Researchers run side by side, up to the `subagentMaxConcurrent` setting, and each returns claims as JSON, every claim with the URL of the page it came from. A claim with no real URL is dropped.
3. **Gaps (deep only).** A second model call names up to 3 follow-up questions for what is missing or contradictory, and those run as a second pass.
4. **Lead.** A final model call writes the answer from the sourced claims only, cites each sentence as `[n]`, notes where sources disagree and leaves out claims the better-supported ones contradict. The numbers come from the same registry as `web_search`, so the chips and the sources list under the reply work as usual.

The model sees only the answer and its citation numbers. The trail (plan, per-question status and source count, sources considered, number of dropped claims) is attached to the tool call and shown above the reply as a collapsed "Researched N questions" line; the researchers also appear in the subagent thread while they work.

## Cost and limits

Researchers are charged to the reply's budget like any subagent, and stop with it. A `normal` run is about a plan call, three researchers and a lead call; `deep` can be six researchers, a gap call, up to three more researchers and the lead, so use it when breadth matters. The plan, gap and lead calls appear in Usage but do not count against the reply's own budget meter. Stop cancels the whole run. Researchers cannot start a research run themselves.
