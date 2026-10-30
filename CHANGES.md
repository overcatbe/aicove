# What's new

## Since v0.3

**Passwords**
- Entries have a **type**, the top folder: web, mail, social, shopping, finance, gov, games,
  media, app, wifi, pc, server, db, vpn, crypto, other. The list is grouped by type with
  headings that fold away, the menu above it shows one type, and the form has a type menu.
- **More → Import CSV** brings in a browser or password-manager export: the logins are listed
  without their passwords, each with a type guessed from its site, to untick, retype or rename;
  then they are encrypted and committed at once, and the CSV can be shredded.

**Pictures**
- The persona writes the image model's whole prompt itself: from your one-line request and what
  the chat is about, 150-250 words with the camera, depth in layers, named light with a warm/cold
  contrast, and the important things in the middle of the frame (pictures often get cropped at
  the top and bottom). What you asked for stays as you said it, and there is no text in the
  picture unless you ask for it. Same image model, much better pictures.

**Chats**
- **Delete** removes a chat for good, with its audio and pictures (no trash any more).

**Writing**
- The persona's picture is next to its name in the talk panel, with the ring that pulses while
  the model thinks, writes or loads.
- After Accept, Decline or Edit the cursor goes on to the next change. Accept all / Decline all
  ask only until you have decided a change by hand; after that they take the rest.
- **New chapter** goes at the end, not after the selected one.

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
