# Alpha Harness — Codex Integration Fork

Hey, this is my fork of [Alpha Harness](https://github.com/residual-lab/alpha-harness).
It keeps the original Alpha Harness features and adds a few things that I found useful while
running alphas every day.

## What this fork adds

- **Codex through your ChatGPT sign-in:** use GPT-6 Astra, Sol, or Luna and GPT-5.6 models in
  the assistant and LLM Power Pool. All GPT models run at Medium reasoning effort.
- **Codex usage visibility:** see your shared ChatGPT allowance and reset time, together with
  the calls and tokens recorded locally by Alpha Harness.
- **Visible Alpha IDs:** see the Alpha ID directly in the Submittable Alphas table.
- **More reliable forced stops:** free local cores when BRAIN does not acknowledge a cancelled
  task. If BRAIN finishes something later, Sync with BRAIN can still recover the result.

## Download and install on Windows

### [Download the latest AlphaHarness.exe](https://github.com/noavailabl/alpha-harness/releases/latest/download/AlphaHarness.exe)

1. Download **AlphaHarness.exe** from the link above.
2. Keep it somewhere easy to find and open it.
3. The first start downloads the files Alpha Harness needs and then opens it in your browser.
   Give it a couple of minutes.
4. Sign in to BRAIN and run **Sync with BRAIN**.

Later, just open the same **AlphaHarness.exe** again.

Windows may say the app is unrecognized because the executable is not code signed. Choose
**More info**, then **Run anyway**.

> On the GitHub release page, download **AlphaHarness.exe** under **Assets**. You do not need
> the Source code ZIP or TAR.GZ files.

## Which version should I download?

Use the release marked **Latest** on the
[Releases page](https://github.com/noavailabl/alpha-harness/releases/latest). The branch is named
`codex-integration-2026.9.24` because this fork started from Alpha Harness 2026.9.24. The branch
name is not the current release number.

## Set up Codex with ChatGPT

The normal Alpha Harness features work without Codex. If you want to use the added Codex models
in the assistant or LLM Power Pool, set this up once:

1. Install Codex CLI using the
   [official Codex guide](https://learn.chatgpt.com/docs/codex/cli).
2. Close and reopen PowerShell after installing it.
3. Run:

   ```powershell
   codex login
   ```

4. Choose **Sign in with ChatGPT** and finish the login in your browser.
5. Check the login:

   ```powershell
   codex login status
   ```

6. Close and reopen Alpha Harness. Go to **LLM Integration** and Codex should show as connected.

If PowerShell says `codex` is not recognized, close and reopen PowerShell first. If it still does
not work, go back to the official Codex guide and finish the CLI installation.

This integration uses the ChatGPT account signed in through Codex. It does not use an OpenAI API
key.

## Updating

Keep the same **AlphaHarness.exe**. When a newer release is available, Alpha Harness offers the
update inside the app.

## Privacy

Your Codex authentication stays in your local Codex sign-in. API keys, authentication tokens,
cookies, BRAIN sessions, Alpha records, runtime databases, and log files are not included in this
repository.

Do not share your Codex `auth.json`, BRAIN session, cookies, or unredacted logs with anyone.

## Credits

All the main Alpha Harness work comes from the
[original residual-lab project](https://github.com/residual-lab/alpha-harness). This fork adds the
changes listed above and keeps the original license.
