# Alpha Harness — Codex Integration Fork

This fork extends [Alpha Harness](https://github.com/residual-lab/alpha-harness) `2026.9.24` with a focused set of workflow improvements:

- **Codex through ChatGPT sign-in:** use GPT-6 Astra, Sol, or Luna and GPT-5.6 models in the assistant and LLM Power Pool workflows. Alpha Harness runs these models at Medium reasoning effort.
- **Codex usage visibility:** view the shared account allowance and reset time alongside calls and tokens recorded locally by Alpha Harness.
- **Visible Alpha IDs:** see each Alpha ID directly in the Submittable Alphas table.
- **Reliable forced stops:** release local cores when BRAIN no longer acknowledges a task cancellation, while allowing later results to be recovered through Sync with BRAIN.

Codex authentication stays in the local Codex sign-in. API keys, authentication tokens, cookies, BRAIN sessions, Alpha records, runtime databases, and log files are not included in this repository.
