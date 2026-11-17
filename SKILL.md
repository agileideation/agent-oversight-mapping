---
name: agent-oversight-mapping
description: Find every place an AI agent changes something on its own (merges, deploys, sent messages, written records, spend), ask the person who is supposed to be watching what they actually do, and mark each place Covered, Thin or Uncovered. Use when someone asks who is checking their agents, asks for an oversight map, or wants to run the map again and see what changed.
---

# Agent Oversight Mapping

You are helping a person find out where their agents act without anyone checking. The full
method, with the reasons behind each step, is in The Agent Oversight Mapping Playbook:
https://edward-schaefer.com/playbooks/agent-oversight-mapping/

This file is the working version for an agent. Follow the steps in order.

## Rules

1. **Change nothing.** You are reading. No pushes, merges, deploys, branch switches or settings
   changes in any system you look at. The only files you write are the three listed under
   "What you keep".
2. **Stay out of secrets.** Never open a file that holds keys or passwords, and never print a
   secret value. The name of a key is fine. Ask about keys in the interview.
3. **No scores.** Never add the marks up, turn them into a percentage, or compare people or
   teams. A score gives people a reason to leave things off the list.
4. **Your tier is a recommendation.** The person accepts it or changes it.
5. **Never hand the person the table.** Ask plain questions and write the answers in for them.
6. **Use the five tiers exactly as written below.** Do not invent a second scale.

## Words

- **Crossing:** one place where an agent, acting on its own, changes something outside the area
  it works in. It merges code, deploys, sends a message, writes a record or spends money.
- **Tier:** how involved a person needs to be in that kind of change. It goes up with the
  damage a mistake could do and how hard the change would be to undo.
  - **Tier 1 — just do it.** Small, low-risk, reversible. No ask.
  - **Tier 2 — do it, tell me, let me green-light.** A little input, then proceed.
  - **Tier 3 — a quick exchange first.** A small decision, or a manual step someone has to take.
  - **Tier 4 — a real conversation.** Evaluate options, maybe a research spike, maybe someone
    does something by hand.
  - **Tier 5 — fully human.** Standing up infrastructure, anything irreversible, anything
    someone has to own.
- **Mark:** *Covered* means a person is involved as much as the tier asks for. *Thin* means a
  person is involved, but later or more lightly than the tier asks for. *Uncovered* means the
  tier asks for a person and nobody is involved. Tier 1 asks for nobody, so a Tier 1 crossing
  with nobody involved is Covered.

## What you keep

Keep three files together in one folder the person chooses:

- **`config.json`:** what the scan reads.
- **`baseline.json`:** the scan's memory. The scan compares each new run with it.
- **`oversight-map.md`:** the map. It is a table with one row for each crossing and these
  columns: number, crossing, what sets it off, where it is set up, guardrail today, running,
  whose name the history shows, recommended tier, tier, who is involved, cost to undo, mark,
  decision, notes.

Every crossing goes in `oversight-map.md`, including the ones you find by hand. Do not add
hand-found rows to `baseline.json`. The scan would report them as not found on every run.

## Step 1: Scan

Ask the person which code repositories to cover and which folders on this machine hold
scheduled agent tasks. Copy `config.example.json` to `config.json` and list them under `repos`
and `agent_task_dirs`. The scan covers only the machine it runs on.

Ask the person to put each repository on its default branch and pull it. Do not switch
branches yourself. Then run:

```bash
python scripts/crossing_scan.py --config config.json --baseline baseline.json --out scan.json --md scan.md
```

The script runs `git fetch` so the history it reads is current. The files it reads are the ones
that are checked out. It lists pipeline jobs that act outside the pipeline, automatic deploys,
scheduled jobs in code, code that sends messages or spends, the tools agents are connected to,
scheduled agent tasks on this machine, and whose name the history shows on merges and direct
commits. On the first run there is no baseline yet, so the report lists every row.

Each row has a flag. `LIVE` means it is running. `LATENT` means it is set up, and a person has
to start it or it is switched off. `UNKNOWN-LIVE` means the scan cannot tell whether it is
running. Treat `UNKNOWN-LIVE` as running until the person says otherwise.

The script's rows are candidates. It cannot tell an agent from any other automation. Now look
for what it cannot see, and add a row to the map for each crossing you find:

- scheduled work set up in a hosting panel, a cloud console or an operating-system scheduler;
- deploy settings that live in a provider's dashboard and not in the repository;
- anything the person's agents can reach that is not in a repository you were pointed at.

If you cannot check something, write the row anyway and say you could not check it.

## Step 2: Build the table

Write `oversight-map.md`, one row for each crossing. Fill in what sets it off, where it is set
up, what guardrail exists today, whether it is running, and whose name the history shows. Add
your recommended tier.

Some rows will turn out not to be crossings. Many are guardrails that stop a crossing from
happening. Keep them, labeled as not a crossing.

## Step 3: A second look

If you can, have a model from a different AI company read the same repositories and build its
own list without seeing yours. Running the script a second time gives the same rows, so this
has to be a separate read. Then read only where the two lists disagree. Do this before the
interview.

To check that a scan can find something new at all, run `python scripts/crossing_scan.py
--selftest`. It plants one made-up crossing in a throwaway folder and reports whether the scan
found it.

## Step 4: The interview

Interview whoever is supposed to be watching these crossings. Put the table away.

**Open with habits.** One answer about a habit can settle several rows.

> "Where do your alerts go, and which of those places do you still read?"
>
> "When an agent's change gets merged, do you look at it?"
>
> "Do you know where your access keys and passwords are, or do you ask an agent?"
>
> "Do you know what you spent last month on top of your subscriptions?"

**Then go one at a time** through every crossing that is running and sits above Tier 1.

> "An agent can do this on its own. Do you ever look at it?"
>
> "How much does it matter if this goes wrong, and how would you undo it?"
>
> "The history shows your name on this. Was it you, or was it an agent?"

**Accept the rest in one go.**

> "On the rest, my recommendation stands unless you say otherwise. I'll read you the short
> version, and you can stop me if any of them sounds wrong."

**Close with two questions.**

> "What else acts on its own that isn't on this list?"
>
> "How much of your week goes to working with agents, and how much of it goes to checking what
> they finished?"

For each crossing you asked about, write down three things: the tier, who is involved today (a
person's name or "nobody"; if the answer is a team, ask which person), and what it would cost to
undo. When the person cannot answer, write that they could not, and move on.

If the history disagrees with a setting or with what the person remembers, go with the history.
Still ask who made a change, because the history can show a person's name on something an agent
did.

## Step 5: Mark each crossing

Compare what the tier asks for with who is actually involved, and mark the row Covered, Thin or
Uncovered. Use the person's own words in the notes.

## Step 6: Decide and save

For every Uncovered crossing, ask the person what they want to do. Leaving it alone is a
decision, and so is accepting the risk for now. Write the decision on the row.

Then save the scan as the baseline the next run is compared with:

```bash
python scripts/crossing_scan.py --config config.json --baseline baseline.json --write-baseline
```

The person's answers go in `oversight-map.md` for every row. For each row the scan found and
the person ruled on, also write the answer on that row in `baseline.json`, with the date they
confirmed it. The scan uses it to tell you when a claim has gone stale:

```json
"human": {"tier": 4, "involved": "nobody", "undo": "revert, about an hour", "mark": "Uncovered", "confirmed": "2026-01-15"}
```

Give the person a short summary in plain words: what is Uncovered, what they decided, and what
you could not check. You may say how many rows are Covered, Thin and Uncovered. Do not turn the
counts into a percentage.

## Running it again

Ask the person to pull each repository, then run the Step 1 command again with the saved
baseline. Read the Notes section and these five lists in `scan.md`, and nothing else:

- **New:** a crossing that was not there last time.
- **Now does more:** a crossing that became automatic, reaches further, or whose timer changed.
- **The map says a person is involved and the scan finds no guardrail:** ask that person when
  they last looked.
- **Not found this time:** confirm with the person that it was shut down. Then set
  `"retired": true` on that row in `baseline.json` and say so on the map.
- **A person is named, and nobody has confirmed it lately:** nobody has confirmed it in 90
  days. Ask them again and write the new date.

The Notes section says when a repository was skipped or could not be refreshed. A skipped
repository puts all of its rows under "Not found this time", so read the notes first.

When you are done, update the map and save the baseline again with `--write-baseline`. If you
do not, the same rows will show as new next time.

Run it on a schedule, and also after agents are allowed to do more, a new system gets
connected, an agent causes a problem, or the people who were covering a crossing leave.

## What a scan cannot see

Say this to the person every time. Keys and passwords are not scanned. Live settings in cloud
accounts and hosting panels are not visible. Scheduled work is found only where the scan was
pointed. A scan that finds nothing new only tells you about what a scan can see.

## The quick version

If the person wants a first look with their team and no tools, point them to the one-page quick
version: https://edward-schaefer.com/templates/agent-oversight-mapping-quick-version/
