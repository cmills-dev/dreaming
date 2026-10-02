# Dreaming

An overnight pass over a day's notes, capped and checked.

Every night at 04:30 Dreaming reads what was written that day in a folder of Markdown notes,
pairs notes that look related, and asks a small AI judge whether each pair is related, a
duplicate or a contradiction. By morning it leaves a short report. In its first version it
changes no note unless you switch writes on, and even then it touches only two frontmatter
fields. Everything else is a flag or a proposal for you to act on.

It was built as one part of a personal agent system, the
[Life Tracker](https://github.com/Cameron0404/life-tracker-kit), and runs on its own here.

## Why

Related notes drift apart, duplicates pile up, and two notes can disagree about the same fact
(a deadline, a room, an amount). I wanted a nightly pass that finds these without giving a
model free rein over my records while nobody is watching. So the design starts from what the
model may not do:

- It never deletes, renames, moves or sends anything.
- The judge has no tools, no MCP servers and a spending cap on every call.
- The judge's answer is data, not an instruction. A verdict must quote one whole line from each
  note, exactly, and the code checks every quote against the file. A reply that misquotes, or
  carries any key it was not asked for, is thrown away.
- Text inside a note that is worded like an instruction is compared, never obeyed. The
  selftest plants one to prove it.

Why a report and not an editor: replayed against 90 to-dos I had closed over two weeks, the
proposed overnight close rule would have closed none of them. That is a measurement on my own
private notes, which this repository cannot reproduce. The people and jobs that close things
already do it in the moment. So the first version reports, and writes stay off until a
week of dry runs has been reviewed.

## What a night does

| Verb | What v1 does |
|---|---|
| Link related notes | writes `related:` on both sides, only when writes are on |
| Mark duplicates | writes `dup_candidate: <name>` on the newer open note, when writes are on. Never `duplicate_of:` or `status:` |
| Flag contradictions | in the report, both sides quoted with their dates |
| Promote into a knowledge bank | a proposal naming the note with no bank page |
| Promote into memory | a proposal quoting your own dictated words verbatim |
| Mark stale | a proposal, keyed on a `supersedes:` line |
| Close finished loops | a proposal only, and never for an item a course site, calendar or triage step owns |
| Write the report | every night |

## Architecture

```
launchd 04:30
  └─ dreaming.sh                 loads dreaming.env, picks --judge and --write, keeps the log
       └─ dreaming.py run --judge
            gather       files written in the night, from an allow-list, after realpath
            candidates   pairs by shared title words (no embeddings), capped at 30 a night
            rules        duplicates and date contradictions found without a model
            judge        one `claude -p` per pair: Haiku, no tools, no MCP, $0.10 a call
            check        enum and whole-line quotes only, everything else discarded
            writer       two fields, compare and swap on sha256, one atomic write, undo log
            report       <state>/<night>/report.md and <state>/latest.md
```

The files:

| File | What it is |
|---|---|
| `dreaming.py` | the whole pass, standard library only (its docstring is the reference) |
| `judge-brief.md` | the judge's instructions, sent before the two fenced notes |
| `dreaming.sh` | the nightly wrapper: settings, log, and the dry-run rehearsal |
| `selftest.py` | 186 offline checks, no network and no real notes |
| `frontmatter.py`, `atomicio.py`, `usage.py`, `ledger.py` | small helpers: the frontmatter reader and writer, the atomic write, the Claude usage reader, the session ledger reader |
| `install.sh` | writes `dreaming.env`, runs the selftest, installs the LaunchAgent |
| `launchd/` | the LaunchAgent template |
| `fixtures/` | invented example notes for the selftest |

The judge is deliberately one tool-less call per pair, not a full agent run. An agent run with
no time cap could still be going when the morning starts, and a call with no tools cannot do
anything but answer.

### Limits built in

- At most 30 judge calls a night, $0.10 each and $1.50 a night, 90 seconds a call. Pairs over
  the cap carry to the next night, and are dropped and counted after 7 nights.
- Usage is checked before every call against the Claude usage snapshot (see Configuration).
  At the five-hour wind-down threshold (90% by default) or the weekly ceiling it stops and
  carries the rest. A usage-limit reply from `claude` stops it too.
- The judge stops at 06:40, so the report lands before the morning. A scheduled run that starts
  after 06:00 does nothing and says "skipped: late start".
- Three unusable replies in a row (a logged-out `claude`, say) end the night as `judge: failed`,
  so a broken judge never reads as a quiet night.
- With writes on: at most 10 writes a night, only in `obligations/` and `vault/prep/`, never
  `status`, `duplicate_of`, `done_at` or `stale`, and never a note someone else edited in the
  last 12 hours.
- A privacy wall: a private note (job applications, `obligations/co-*`, mail, memory) is never
  linked to a study note (`vault/prep/`, `vault/exams/`, `vault/revision/`), and no value
  naming a term from `DREAMING_PRIVATE_TERMS` is ever written.
- Live judge calls and writes are refused (exit 5) when `DREAMING_HEADLESS` is set, or any
  variable named in `DREAMING_HEADLESS_VARS`. Point that at the marker your agent runner sets
  for a managed run (for the Steward runner, `STEWARD_RUN_ID`), so an automated build
  can test Dreaming but never run it live.

## Requirements

- macOS for the scheduled job (launchd). The script itself runs on any system with Python.
- Python 3.9 or later. Standard library only, nothing to install.
- git. The notes folder works best as a git repository, because commit times date the notes.
- For live nights, the [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI,
  installed and logged in. Dry runs and the selftest need no Claude account.

## Setup, step by step

1. Clone the repository somewhere permanent, because the LaunchAgent points at it.

   ```bash
   git clone https://github.com/Cameron0404/dreaming.git ~/tools/dreaming
   cd ~/tools/dreaming
   ```

2. Run the selftest. It needs nothing but Python and should end with
   `all dreaming checks passed (all)`.

   ```bash
   python3 selftest.py
   ```

3. Lay out your notes folder. Dreaming reads these paths under it, and any that are missing are
   simply read as empty:

   ```
   <notes>/obligations/*.md                 to-dos and deadlines, one per file, with frontmatter
   <notes>/vault/**/*.md                    notes (vault/prep/ is the writable part)
   <notes>/reference/**/*.md                reference notes, read only
   <notes>/_sources/outlook/*.md            received mail saved as Markdown (sent mail is skipped)
   <notes>/_sources/transcripts/lectures/*.txt
   <notes>/_meta/logs/session-*.md          Claude Code session logs
   <notes>/_meta/session-ledger.md          a session ledger
   <notes>/_meta/agent-runs/...             agent run reports and progress files
   ```

   And these under your home folder:

   ```
   ~/vault/raw/inbox/dictations/*.md        dictated notes, with a "## What was said, verbatim" block
   ~/Documents/Transcripts/**/*.md          meeting transcripts
   ~/.claude/projects/*/memory/*.md         Claude Code memory files
   ```

   A note's useful frontmatter fields are `title`, `status` (`open`, `done`, `stale` and so on),
   `due` or `due_utc`, `assignment_id` or `event_uid`, `class` (`hard-external` marks something
   only you can confirm) and `supersedes`. Look in `fixtures/repo/` for examples.

4. Install the Claude Code CLI and log in, if you want live nights:

   ```bash
   claude --version
   ```

5. Write the settings file and check it. This copies `.env.example` to `dreaming.env`
   (gitignored), fills in your notes folder and runs the selftest again.

   ```bash
   ./install.sh --root ~/notes
   ```

   Open `dreaming.env` and check `DREAMING_TZ`. Left unset, Dreaming uses the Mac's own zone.

6. Rehearse a night. A dry run makes no Claude call and keeps its state in a throwaway folder,
   so it never moves the cursor the real job reads.

   ```bash
   bash dreaming.sh --dry-run
   tail -20 ~/notes/_meta/briefs/dreaming.log
   ```

7. Schedule it. This installs `~/Library/LaunchAgents/com.example.dreaming.plist` for 04:30
   daily. Set `DREAMING_LABEL` first if you want a different label.

   ```bash
   ./install.sh --root ~/notes --launchd
   ```

8. After a week of dry-run nights, the seventh night writes a go-live question to
   `<notes>/_meta/health/questions/dreaming-go-live.json` with the week's numbers. Silence means
   no. To switch writes on, create one empty file:

   ```bash
   touch ~/notes/_meta/health/dreams/state/writes-enabled
   ```

To remove the schedule: `./install.sh --uninstall`.

## Configuration

Everything is an environment variable, read from `dreaming.env` by `dreaming.sh`. Only the
first is required.

| Variable | Default | What it does |
|---|---|---|
| `DREAMING_ROOT` | the current directory | the notes folder |
| `DREAMING_HOME` | `~` | the home for dictations, meetings and memory |
| `DREAMING_STATE` | `<root>/_meta/health/dreams` | state and nightly reports |
| `DREAMING_TZ` | the Mac's own zone, else UTC | the time zone a night is measured in |
| `DREAMING_DICTATIONS_DIR` | `vault/raw/inbox/dictations` | dictations, relative to home |
| `DREAMING_MEETINGS_DIR` | `Documents/Transcripts` | meeting transcripts, relative to home |
| `DREAMING_DENIED_NAMES` | `career` | folders never read or written, under home or beside the root, comma-separated |
| `DREAMING_HEADLESS_VARS` | none | your agent runner's markers, comma-separated, that block live runs |
| `DREAMING_CLAUDE` | `claude` on PATH | the Claude Code CLI |
| `DREAMING_MODEL` | `haiku` | the judge model |
| `DREAMING_MEMORY_DIRS` | every `~/.claude/projects/*/memory` | memory folders, `:`-separated |
| `DREAMING_PRIVATE_TERMS` | none | a file of terms, one per line, never written into a note |
| `DREAMING_BANK_CMD` | none | a command that lists knowledge bank pages for a title, as JSON |
| `DREAMING_USAGE_FILE` | `<root>/_meta/health/usage.json` | the Claude usage snapshot |
| `DREAMING_LOG` | `<root>/_meta/briefs/dreaming.log` | the wrapper's log |

The usage snapshot is a JSON file that a Claude Code status line script writes:
`{"_written": "<ISO time>", "data": <the status line's JSON>}`. Without one, the first live
call goes ahead and a usage-limit reply stops the night. The five-hour threshold is read from
`<root>/_meta/health/threshold-override`, then `<root>/_meta/health/threshold`, then 90.

Keep the state folder out of git: add `_meta/health/` to your notes folder's `.gitignore`. The
reports quote your notes.

## Usage

```bash
python3 dreaming.py status                    # latest report, mode, night k of 7, carry
python3 dreaming.py status --json             # mode, night, changed, conflicts, top
cat ~/notes/_meta/health/dreams/latest.md     # the whole report
python3 dreaming.py gather --night 2026-03-11 --json       # what a night read
python3 dreaming.py candidates --night 2026-03-11          # the pairs, read only
python3 dreaming.py run --judge-replay verdicts.jsonl      # replay recorded verdicts
python3 dreaming.py undo 2026-03-12#3                      # put one write back
python3 selftest.py --only judge                           # one part of the selftest
```

Avoid testing with a bare `dreaming.py run`: without `--state` it uses the real state and moves
the cursor. Use `bash dreaming.sh --dry-run` instead.

A report looks like this:

```
Dreaming Thu 12 Mar: dry run, night 2 of 7
judge: ran 5 calls, $0.02, 1 min
inputs: 14 files (obligations 2, vault 3, session 1, ...), 14 new since last read, 0 refused
changed: 0
conflicts: 1
  1. Boiler service: engineer visit (2026-03-02, git line-add) vs visit-plan (2026-03-11, quoted line)
proposals: 2
  1. ask owner: admin-car-inspection-appointment passed its date (Wed 11 Mar 08:30), is it finished? -> tick done on the site
  2. stale vault/prep/reading/old-list.md: list.md:6 "supersedes: old-list.md" -> set stale: true by hand
pairs: 5, carried: 0, expired: 0
```

`undo` restores a field only if it still holds exactly what Dreaming wrote. If anyone changed it
since, it prints `undo skipped: value changed by another writer` and exits 3. An undo freezes
that rule for 7 days.

For a dashboard, `dreaming.surface_text("email")` (or `"master"`, `"pane"`, `"phone"`) returns
one line read from the last run's state and nothing else.

## Limitations

- Most of what a night reads produces nothing yet. Session logs, the ledger, run reports,
  progress files, lectures and meeting transcripts are read and counted, but using them for
  links and contradictions is a v2 decision.
- The close signal is weak: a one-off item whose date fell in the night "looks finished", which
  nothing proves. That is why it stays a proposal.
- Pairing is by shared title words, so two notes about the same thing under different titles are
  missed.
- The time slots (04:30 start, 06:00 late start, 06:40 deadline) are fixed in the code, in the
  `DREAMING_TZ` zone, while launchd fires in the Mac's own zone. If you set the two apart by
  hours, change the plist's slot.
- The layout is the one the Life Tracker uses. Other layouts need small changes to
  `Roots.sources()`.

## Licence

MIT, see `LICENSE`. Written by Cameron Mills. The design, the specification and the checks are
mine, and the code was written by Claude Code agents to that specification.
