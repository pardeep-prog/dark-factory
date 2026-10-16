# Installing the Dark Factory skills

One tool, `scripts/df-skills`, puts the skills where Claude can use them. Python 3 standard library only.

| Where you use Claude | What to run | Result |
|---|---|---|
| Claude Code (terminal, IDE, Code tab) | `scripts/df-skills install` | each skill is linked into `~/.claude/skills`; start a new session |
| Claude chat / Cowork / Desktop chat | `scripts/df-skills zip` | one `.zip` per skill in `dist/skills/`, ready to upload |

```bash
scripts/df-skills list                  # every skill, and whether it suits chat
scripts/df-skills check                 # validate names and descriptions, writes nothing
scripts/df-skills install               # all skills into ~/.claude/skills (links, safe to re-run)
scripts/df-skills install --only df-qa,df-product-owner
scripts/df-skills zip                   # the chat set: product owner, requirements, QA, adversary gate, video intake
scripts/df-skills zip --set all         # every skill
scripts/df-skills zip --bundle          # also one archive holding all the zips, to hand to someone
```

## Chat and Cowork

Upload each zip in Settings, under the skills section. The zip holds the skill folder (`SKILL.md` plus any
scripts or references, links followed), which is what Claude expects.

- The default set is the skills that work without a terminal: they turn ideas, videos and results into
  requirements and verdicts. The rest need code, a cluster or the kit.
- The tool warns when a skill mentions something chat cannot run (for example `ffmpeg`); those parts are
  simply skipped there, the rest still works.
- An uploaded skill does not follow the repo. After a skill changes, run `df-skills zip` again and re-upload.

## Claude Code

`install` links instead of copying, so `git pull` updates the skills. It repoints a stale link (repo moved),
and it never overwrites a real folder of the same name. Start a new session to see the skills.
