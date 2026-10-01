# Install and start learning

Lucerna 0.3.0 requires Python 3.10 or newer, a modern browser, and an AI host that can read local skills and run commands. Its local runtime dependencies are included. No additional model account or API key is required.

## Install the skill

Extract the ZIP normally. Keep the complete `lucerna` folder—the folder containing `SKILL.md`—together with its `scripts`, `references`, `agents` and `sources` contents.

For Codex, place that folder inside your Codex skills directory. The default is `%USERPROFILE%\.codex\skills` on Windows or `~/.codex/skills` on macOS/Linux; a custom `CODEX_HOME` uses its own `skills` directory. The resulting path ends in `skills/lucerna/SKILL.md`.

Once your host discovers the skill, ask:

> Use $lucerna. Help me start a learning workspace and explain something I have been trying to understand.

Your agent can initialize and open the workspace for you. Choose a normal data folder such as Documents/Lucerna Workspace. Keep using that folder so papers, useful context and learning paths remain available between conversations. It is separate from the installed skill, so replacing the skill during an upgrade preserves your learning data.

If you already use Teaching & Explanation, keep the same learning-home folder. Install the complete `lucerna` skill folder, confirm the host discovers `$lucerna`, and remove the superseded `teaching-explanation` skill folder to avoid duplicate discovery. Your learning workspace stays where it is.

## Open the workspace directly

You can also start it yourself from a terminal opened in the extracted or installed `lucerna` folder. In Windows PowerShell:

```powershell
python scripts/teacher.py --home "$env:USERPROFILE\Documents\Lucerna Workspace" init
python scripts/teacher.py --home "$env:USERPROFILE\Documents\Lucerna Workspace" serve
```

On macOS/Linux:

```sh
python3 scripts/teacher.py --home "$HOME/Lucerna Workspace" init
python3 scripts/teacher.py --home "$HOME/Lucerna Workspace" serve
```

Open the local URL printed by `serve`, and leave that process running while you use the workspace. Stop it with Ctrl+C when finished. Restart it with the same home to return to the same library and learning state.

For an immediate working example, choose one of the original labs on the Library page. Inside attention lets you point a query vector and inspect the resulting weights, then change a causal mask. The memory, chemistry and oscillator labs show other kinds of mechanisms. You can also install a lab with `python scripts/teacher.py --home "LEARNING_HOME" showcase attention`.

Use Search your sources (Ctrl+K) to find matching passages, or + Add source to import an arXiv link or a local source file. Ask your agent to explain that source and create the useful scenes. In the viewer, Save question preserves a question with its source and scene context; Copy to my conversation lets you bring it into your conversation immediately. Your active agent answers and publishes revisions. Reopen the explanation or use its refresh action to load the latest version.

An explicit `--home` selects your learning folder. For automatic selection, set `LUCERNA_HOME` to its absolute path; the earlier `TEACHING_HOME` setting remains supported. Lucerna also reuses an existing legacy default workspace when neither setting is present. Keep an existing home rather than creating a second copy of your learning state.

## Make the workspace comfortable

Choose Visual skin in the header. LAMPLIGHT is the original warm instrument; APERTURE has an optical navigation dock and suspended source panes; MARGIN is a bound research folio with a source register and annotation margin. Try a choice with a source or experiment open: its interface body changes without resetting the scene. The setting belongs to this browser, not the learning-home folder. Exported explorations include the complete skins, including their artwork. If a change does not survive reopening, browser storage may be unavailable; select it again for the open page. Choose LAMPLIGHT to return to the original appearance without changing your learning data.

## Continue, move or upgrade

Tell your agent which learning home to use when opening a new task. It can recover your remembered context, unresolved questions and current pathways from that home.

To move the whole workspace, stop the server and copy the complete learning-home folder. Open the copied home with the same `--home` option. To share a single exploration, use Export ↗ in the reader. A standalone export contains its source and authored scenes; your full learner profile and curricula remain in the local workspace.

When upgrading the skill, replace its installed folder and retain the learning home. Keep a copy of the old package and a stopped-workspace backup when you want a straightforward way to return to an earlier installation.

## If something does not open

If `python` is not found on Windows, use `py -3` in place of `python` if the Python launcher is installed. Confirm that the interpreter is at least version 3.10. If the page reports that the local workspace is unavailable, restart `serve` with the same home and open its newly printed URL.

If narration is unavailable, use the transcript or install a voice through your operating system's normal speech settings. If a PDF is scanned or its equations extract poorly, provide a text-bearing PDF, HTML source or an OCR result through your host. Source imports and explanatory authorship remain separate steps; a readable source without scenes is ready for your agent to work on.
