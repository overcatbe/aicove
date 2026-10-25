# What's new

## v0.3 (2026-10-25)

A lot was reworked since v0.22 (2026-10-19): agents stand on their own, personas keep settings per model,
the Writing tab learned to work chapter by chapter, and the Tools tab got groups.

**Agents**
- Agents stand on their own: a picture, an optional voice, files they read, no persona behind
  them. In the Chat tab they are below the personas (as `@name`) and know their reports.
- **Leads**: a run brings many headlines, each with why and one link, for you to pick from. A
  lead can follow up an earlier report.
- **Research…**: one topic in depth, older sources welcome. It shows source cards as it reads,
  then writes a dossier: what is solid, what is disputed, open questions, proposed articles and
  all sources. **Write articles…** has a persona write them from the dossier, facts only from
  its sources.
- **Write it with…** starts a Writing project from a report. Both it and **Work on it…** offer
  the persona you picked last time.
- Stories need articles from two sites, no front pages, at most three days old; the agent
  knows today's date.

**Chat**
- 👍/👎 under answers, counted per persona and model. **Again with…** answers the last question
  anew on another model.
- Personas keep their settings per model, one file per machine: **Keep for <model>**, and
  **Settings for:** on the Personas tab. **Clean up…** forgets models unused for 30 days.
- The model list offers every launcher, the persona's own on top.
- **Summarise** next to the context meter; read web pages and pictures are counted, so a chat
  is summarised before it overflows.
- A ring around the persona's picture pulses while the model thinks, writes or loads.
- Models with strict chat templates (Mistral Nemo, Gutenberg) no longer fail with "roles must
  alternate".
- The box you write in can be made taller, and stays so.

**Writing**
- One talk per chapter: go to another chapter and you get its talk. **New talk** starts afresh.
- A first prompt waits in the box on a chapter with no talk yet (`first-prompt.txt`, yours to
  change).
- **Cast…**: tick who is in the chapter, and only their cast entries go to the model, plus
  anyone you name in your message. Without a pick, the people named in the chapter go.
- When the story memory has to shrink, the chapter notes keep their facts.
- The talk sends as many recent messages as fit, so a long talk never overflows the model.
- A **Folder** button opens the project's folder.
- Fixed: a rewrite longer than the chapter crashed; many waiting changes froze the window.

**Tools**
- Groups: a `group:` line puts a tool under a heading; click a heading to fold it (it shows how
  many are running). The groups are yours to name.

**Everything else**
- Sync: the AIcove folder and its stories follow their git remote on their own.
- A SearXNG among your tools is started by the first web search.
- Fixed: AIcove could close when an agent's model came up; Save on a persona could land on the
  wrong one.
