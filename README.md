# Agent Oversight Mapping

If your agents can merge code, deploy, send messages or spend money on their own, could you list
every place that happens and say who checks it? Agent Oversight Mapping is a practice for
finding out.

This repository holds the working pieces:

- **`SKILL.md`**: instructions an AI agent can follow to run the practice with you. It works as
  an Agent Skill, or you can point any agent at the file.
- **`scripts/crossing_scan.py`**: a read-only scan of your code repositories and your machine.
  It lists places where something can act without a person, and on later runs it tells you
  what changed. Its rows are candidates. The interview decides what they mean.

The method is written up in
[The Agent Oversight Mapping Playbook](https://edward-schaefer.com/playbooks/agent-oversight-mapping/).
If you want a first look with your team and no tools, start with
[the quick version](https://edward-schaefer.com/templates/agent-oversight-mapping-quick-version/).

## What you need

- A recent Python 3. The scan uses the standard library only.
- `git`, and a local copy of each repository you want scanned.
- An AI agent to run the interview, or a person who will ask the questions in `SKILL.md`.

## Run the scan

```bash
cp config.example.json config.json
```

Edit `config.json` to list your repositories. Pull each one first. Then:

```bash
python scripts/crossing_scan.py --config config.json --baseline baseline.json --md scan.md
```

Read `scan.md`. The first run lists every row. To save it, so the next run shows only what
changed:

```bash
python scripts/crossing_scan.py --config config.json --baseline baseline.json --write-baseline
```

To check that the scan can find something new, run:

```bash
python scripts/crossing_scan.py --selftest
```

## What the scan does and does not do

- It reads. It does not push, merge, deploy or change a setting. It runs `git fetch` so the
  history it reads is current. The files it reads are the ones you have checked out, so pull
  first. Pass `--no-fetch` to skip the fetch.
- It does not open files named like secret files (`.env`, keys, credentials), and it replaces
  anything shaped like a token in the evidence it records.
- It does not see keys, live settings in cloud accounts, or scheduled work set up outside the
  places you pointed it at. Ask about those in the interview.
- It cannot tell an agent from any other automation. That is a question for the interview.
- It gives no score. It reports counts and what changed.

It reads git history, GitLab pipelines and GitHub Actions workflows, a handful of deploy config
files, schedules written in code, known messaging and model-provider hosts, agent tool
connections, and scheduled agent tasks on the machine. You can add your own hosts and folders
in `config.json`. It looks for patterns and will miss things. A second read by a different
model, and the interview, are there to catch what it misses.

## Tell me what you found

If you run this, I would like to hear what it turned up. Open an issue here.

## License

The script is licensed under the [MIT License](LICENSE). `SKILL.md` and this README are
licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), the same as the quick
version.

By Edward Schaefer.
