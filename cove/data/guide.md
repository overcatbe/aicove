# AIcove

## Start here

AIcove is one window for the things you run on this machine: local AI models and the personas
you chat with, your other tools, and your passwords.

To chat: the **Chat** tab. Pick a persona, write, press Enter. If its model isn't loaded,
AIcove loads it first. See the topic *Chat*. When you are done: **Stop everything** (top right).

Models are set up on the **Launchers** tab (a model file and how it runs), personas on the
**Personas** tab (who you talk to, and on which models).

Only one model is loaded at a time. Starting another one stops the one that was running.

The pane at the bottom of the window shows what the buttons did. If something doesn't work,
the reason is usually written there.

## Chat

The **Chat** tab is AIcove's own chat, no browser involved. On the left your personas and the
chats of the one you picked; on the right the chat.

- **Assistant** is a plain persona with no prompt, knowledge or facts, on whatever model is
  loaded: for trying a model as it is. Delete it if you don't need it; it doesn't come back.
- **Balthazar the Unsupervised** is an example of what a persona can be: an ancient, grumpy
  wizard who holds grudges. Change him on the Personas tab, or delete him; he doesn't come back either.
- **Enter** sends, **Shift+Enter** makes a new line. **Ctrl+N** or **New chat** starts a new one.
- At the top: the model the persona runs on. A persona names its models (`models:` in its file,
  set on the Personas tab); the list shows the ones this machine has. If another model is loaded,
  sending loads this one first (that stops the other). **Load** does it without sending.
- **Stop** ends an answer early; what came so far is kept.
- Under each message: **edit**, **delete**, and for the last answer **answer again**. Editing
  your own message offers **Save and answer again**: everything after it goes, and the persona
  answers anew.
- **answer again** keeps the answer it replaces: **◀ 2/3 ▶** under the last answer switches
  between them, and the one shown is what the chat goes on with. When you write your next
  message, the others are dropped.
- **Picture…** (or drop or paste a picture into the box) sends a picture with your message,
  for a model that can see: a launcher with an **eyes** file (mmproj), marked 👁 wherever models
  are listed. If the chosen model can't see, AIcove offers to load one that can; **Send
  anyway** tells the model a picture is there without showing it. Big pictures are made smaller first (1536 px at most). They are
  kept in the persona's `chats/pictures/` folder; click one to open it.
- **★ keep** under an answer puts it into the persona's **Best of…** (the button at the top). To
  keep just a line, select it and use the right mouse button: **★ Add to … best of**. It is a
  plain file, `best-of.md` in the persona's folder, with the date and chat of each entry; edit
  it in **Best of…** or any editor.
- After each answer: how long it was and how fast it came, e.g. *312 tokens · 18.4 t/s*
  (thinking counts too). Handy for comparing models and launcher settings.
- **▸ Thinking** opens what a thinking model considered before answering. It is kept with the
  answer but never sent back to the model.

Every chat is a file in the persona's folder, `~/AIcove/personas/<persona>/chats/`. Deleted
chats go to `~/AIcove/personas/.trash/chats/`. **Rename…** changes a chat's title; new chats are
named after their first message.

- A persona with file access shows its steps in the chat: **▸ Read /etc/fstab**, **▸ Looked into
  its own folder** and so on. Click one to see what it got back. Before it writes a file,
  AIcove asks you (see the topic *Letting a model use your files*).

**Long chats.** A model only takes in so much text (the launcher's context size). When a chat
fills 70% of it, the older messages are summarised by the model itself, which takes a moment
("The chat is getting long — summarising…"), and from then on the persona gets that summary
instead of them; the newest part stays word for word. The bar right of the menus,
**Context 5.2k of 32.8k (16%)**, shows how full it is (prompt, facts, knowledge and the chat;
it turns orange from 70%). It shows while the persona's model is loaded. The chat on screen
stays complete:

- The summary shows as **So far** at the top of the chat. **edit** it to correct or add what
  matters; **drop** it and the persona sees everything again (and makes a new summary when
  needed).
- A line marks where it starts: *above this line … sees only the summary*.
- When the chat grows on, the summary is extended. Editing or deleting one of the summarised
  messages drops it, and the next message makes a new one.

**What a persona remembers.** Nothing carries over from one chat to the next by itself, except
the persona's **facts**: short sentences in four sections, *About you*, *Story so far*, *World*
and *Style and rules*. They go into every chat with that persona.

- **Remember…** (top of the chat): the model reads this chat and proposes what is worth keeping.
  You tick, change or drop each one; only **Keep the ticked ones** saves anything.
- **Facts…**: all of them, one tab per section, one fact per line, to edit or delete.
  **Open in the text editor** opens a section's file in your editor instead; **Show files**
  opens the folder with all four.
- Each section is a file: `~/AIcove/personas/<persona>/facts/about-you.md`, `story-so-far.md`,
  `world.md`, `style-and-rules.md`. Editing them by hand is fine.

Keep them short: every fact costs room in every chat.

**Controls** above the input set how this chat answers, over the persona's own settings.
*as set* means the persona's (or its launcher's) settings. Each chat keeps its own; a new chat
starts with *as set*.

- **Style**: *Factual* (focused), *Creative*, *Story* (loosest) — the same presets as on the
  Launchers tab.
- **Thinking**: *on* or *off* for thinking models (off answers quicker). Greyed out when the
  model's thinking can't be switched.
- **Length**: the longest an answer may get: short, medium or long.

**Knowledge** is material a persona works from: notes, lore, books. Put the files into its
`knowledge/` folder (**Knowledge…** at the top of the chat opens it; the button shows how many
files there are). Text, Markdown, PDF, EPUB, Word and LibreOffice files work; PDF, EPUB and Word
are turned into text once and kept in `knowledge/.text/`. It works without file access.

- Small knowledge (up to a quarter of the model's context) goes into every chat as it is.
- Bigger knowledge, such as whole books, is too much for that. The persona then gets a list
  of the files and looks things up when a question touches them: **▸ Searched its knowledge
  for …** and **▸ Read … in its knowledge** show in the chat. This needs a model that can use
  tools; on one that can't, only the beginning of the knowledge is given.

**Web** above the input (off/on, per chat) lets the persona search the web and read pages:
**▸ Searched the web for …** and **▸ Read …** show in the chat. It needs a model that can use
tools; on one that can't, the menu is greyed out. Searching goes to DuckDuckGo, or to your own
SearXNG if the `web_search` setting (Settings tab) is its address, e.g. `http://127.0.0.1:8888`. If that address is a tool of yours (Tools tab) that isn't running, the first search starts it.
After many searches in a short time DuckDuckGo stops answering for a while; Wikipedia's search
stands in until then. Pages on this computer or your home network can never be read, also not
through a redirect, so a model can't reach llama-server or your router this way.

**Voices.** A persona with a voice (Personas tab, **Voice**) gets **🔊 listen** under its answers:
the answer is read aloud, without code and Markdown marks, and **■ stop** ends it. The voice
comes from a voice server (a tool on the Tools tab, started when needed, or an address; see the
README for what it has to answer); its voices are the ones on offer (**Get the voices** on the
Personas tab asks it). Making the audio can take a while, depending on the server and the
machine; once made, it is kept (as MP3, if ffmpeg is there) in the persona's `chats/audio/` and
plays at once. Audio no message needs any more (the chat or message deleted, the answer edited,
another voice) is deleted by itself. The `voice` setting names the tool, or the address of a
voice server running elsewhere.

**Pictures** above the input (off/on, per chat) lets the persona make pictures: ask for one, or
it decides a picture says more. **▸ Made a picture: …** shows what it asked for (click to see the
whole description), the picture comes under it; click the picture to see it full size. The image
server (Tools tab, *Pictures*: stable-diffusion.cpp's sd-server) starts when needed, and a
picture takes a while. Pictures are kept in the persona's `chats/pictures/`; deleting the chat
or the message deletes them. The `pictures` setting names the tool, or the address of an image
server running elsewhere (anything with the OpenAI image API). Needs a model that can use tools.

## Agents

An agent is a persona with a standing task that works on its own and comes back with
reports. The first one is a story hunter: it searches the web for news on your themes and
brings **leads**: a headline, a line or two on what it is and why it is interesting, and the
link, many of them, for you to scan and pick from. Set its **sites** limit to 2 or more and it
writes each story up instead, as a small article from that many different sites, with the facts
to check.

**Reports** shows what the agents brought, newest first; new ones in bold. Filter by
status, theme and agent. A report has a title, a teaser, the article, its sources (only pages
the agent really read in that run) and a list of facts to check, each with the number of its
source.

- **Work on it…** starts a new chat with a persona you pick (the theme's persona if it has
  one, else Writing Buddy), Web on, with the report typed into the input: add what you want
  and send.
- **Keep** / **Used** / **Dismiss**: Dismiss takes it off the list (the file stays). Kept,
  used and dismissed stories go into the agent's next runs as your taste, more or less like
  them; nothing it reported before is reported again.

**Agents** is where you set one up:

- **Persona**: whose voice and facts it has. **Models**: the ticked launchers, in order. A
  run uses the loaded model if it is one of them; with nothing loaded it starts the first one
  this machine has and stops it again afterwards. When another model is loaded (you are
  chatting) a scheduled run waits and tries again at the next check; **Run now** asks whether
  to stop the other model.
- **Schedule**: `manual`, `every 6h`, `daily 07:00, 19:00`, `weekdays 07:00`,
  `mon wed fri 08:30`. It only runs when **Run on schedule on this machine** is ticked: that
  puts one line in your crontab which runs `aicove agent due` every 10 minutes. A run missed
  while the machine was off is made up once.
- **Style** (Factual is good for reports), **Thinking** (off makes a hunt for leads much faster),
  **Limits**: searches, pages read, tool calls, minutes, stories or leads per run, and
  **sites**: 1 = leads, 2 or more = written-up stories from that many sites. Each step of a run
  may think for about 3000 words at most; a model cut off is told to act.
- **Search from**: where its searches and page reads go out from. Normally the machine it runs
  on; or another machine of yours that ssh knows (a `Host` in `~/.ssh/config`) and that has a
  SearXNG as a user service on 127.0.0.1:8081. Then sites see that machine's address, not yours.
  Its SearXNG is started for the run if it isn't running, and stopped again afterwards.
- **Themes**, one per line: `Name | what it means to you | persona to work on it with`.
- **Task**: what it should do, in words.

**Research…** (next to Run now) gives the agent one topic to dig into instead: "Nazi weather
stations and U-boats in the Arctic", "Bilderberg 2026", a name, a question. Old sources are
welcome there (archives, research, books, news of any year, fringe sites marked as fringe). It
reads page after page and notes each source on a card (what the page says, what kind of source
it is), then writes a **dossier** as its report: what is solid, what is disputed or only
claimed and by whom, open questions, 3 to 5 proposed articles with angle, hook and sources, and
all the source cards. On a dossier, **Write articles…** has a persona of your choice write
articles from it (headline, teaser, text) in its own voice, facts only from the dossier's
sources, with wishes like "in German, about 800 words"; they come under Reports too. Research
runs are long: give it more searches, pages and minutes than a news hunt.

A lead may come straight from a search result (the report shows what the result said, or that
the agent read the page); its address must be one the search gave or a page it read. A written-up
story stands on pages the agent read in that run, from as many different sites as **sites**
says. Neither may be a front page (a front page only helps find articles) or older than 3 days
when the page or its address says when it came out. A search for an old year ("October
2024": models like the year they were trained in) is sent back. The agent is told today's and
yesterday's date. For finding stories a persona with a plain or no prompt works best: a
brainstorming persona invents angles and critics; let it work on the story afterwards (the
theme's persona, **Work on it…**).

An agent may search the web and read pages, and save its reports in its own folder. Nothing
else: no files of yours, no commands. Runs go on when the window is closed; **Stop** ends one.
The last run's log is under the form. Small models are weak at long chains of searching and
reading; give them a short, clear task and a few themes.

**Agents of another machine** (one you have launchers of) show under its name. They run there,
on its own schedule; editing, Run now and the reports work from here over ssh. Their reports
are fetched when the window opens and on **Update from other machines**.

Where they are: `~/AIcove/agents/<id>/`: `agent.txt` (the settings, by hand works too),
`stories/` (one Markdown file per report), `seen.txt` (links already reported), `runs/` (a log
per run).

## Writing

For long texts: a novel, a fic. A project is a list of chapters, each one a Markdown file
whose first `# ` line is its title. The top row has the project, the **Project** menu (New
project…, Import…, Export…, Rename…, Delete project…, Open folder, Keep versions), **Story
memory…** and **Copy for AO3**.

Models only work when you ask: Send, a template, Review, Remember or a build of the story
memory. Each such job reads the chapter as it is in the editor at that moment, does its work,
and stops. While you write, nothing runs.

- **Import…**: paste text, or **From file…**: HTML (an AO3 download or a saved page keeps the
  italics best), PDF (needs `pdftotext`; check the paragraphs), Markdown or plain text. It is
  split at chapter headings (`Chapter 3: Title`, `Prologue`, `Part Two`, `XII`), at `#`
  lines, at lines that start with a word you type, or not at all. The list on the right shows
  what it found: untick what isn't a chapter (the title page and tags before the first chapter
  are unticked already). Into a new project, or after the last chapter of the open one.
- **One line is one paragraph**, as in a word processor: Enter starts the next one, and an
  empty line is only there when you put it there. (Projects from before, which had an empty
  line between paragraphs, are taken over once when opened; git keeps the version before.)
- **Copy for AO3** puts the open chapter, without its title, on the clipboard as HTML: a `<p>`
  per paragraph, `<em>` for italics, scene breaks centred. Paste it into AO3's HTML box (or its
  rich text box). Changes still waiting are not in it.
- **The editor**: write or paste. Pasting from a web page or a word processor keeps italics
  and bold as `*italics*` and `**bold**`. Changed text is saved once a minute, on **Ctrl+S**,
  and when you go to another chapter or tab or close the window. A model always reads the text
  as it is in the editor when you ask, saved or not.
- **New chapter** goes after the selected one; ▲ ▼ move it; **Done** marks it finished for now
  (and tells you when its story memory is out of date; it starts nothing); **Remember** reads
  it into the story memory; **Delete** moves it to the project's `.trash` folder.
- **Export…**: the whole project as one file: Markdown, plain text, a web page (HTML), PDF,
  an e-book (EPUB) or Word (.docx), each chapter starting on a new page where that exists.
  All chapters, or only those marked done. Needs no extra programs.
- **Delete project…** moves the whole project to `~/AIcove/writing/.trash`.
- **Keep versions (git)**: each time you leave a chapter you changed (another chapter, another
  tab, closing the window, Ctrl+S) a commit is made in the project's folder, so every earlier
  version can be got back with git. It stays on this machine; nothing is pushed anywhere.
- **The text size**: Ctrl + mouse wheel, or Ctrl+= and Ctrl+-, in the editor. It is remembered.

**Talking about the text** (the panel on the right): pick a persona (it is remembered per
project) and press **Load** if its model isn't running. Make one of your own for this on the
Personas tab, with the prompt that suits how you work: an editor, a writing partner, a critic.
It reads the open chapter as it stands.

- **Select text first** to work on just that passage; with nothing selected, questions are
  about the whole chapter and new text goes in at the cursor.
- **Ask or tell it** in the box (Enter sends). A question gets an answer in the panel. When you
  ask for a change ("fix my English here", "more ominous", "expand this"), the new version comes
  into the chapter as **changes**: old text struck through in red, new text in green.
- **Templates** are prompts for jobs you do often, text files in `~/AIcove/writing/templates/`
  (the **…** button opens the folder). It starts with three, one of each kind, to change or
  replace with your own: **English polish** (`on: passage`: changes text, on the selection or
  passage by passage over the whole chapter, ✎ in the list), **Review** (`on: review`: notes
  only, see below) and **Feedback** (`on: chat`: sent as a message in the talk).
- **Stop** (in place of Send while it works) ends the answer or the template run there. What
  came so far is kept; changes already put into the text stay waiting for you.
- **Think**: lets a thinking model think first. Better sometimes, much slower; templates that
  change text never think.
- **Put the last answer into the text**: when a model gave a new version without marking it,
  this takes it in as changes anyway.
- The rules every request carries: the text is yours, dark themes, violence, sex and crude
  language stay exactly as written; nothing is softened, cut or moralised; your voice, tense and
  spelling stay. Use an uncensored model for writing like that.

**Review** (the second tab of the right panel): notes on the text, never changes.

- **Check** counts, without a model: phrases from your **Slop list…** (a text file, one phrase
  per line, `*` for any one word: change it to your taste), many gestures close together
  (smirks, shrugs, eyebrows…), long runs of dialogue with little around them, the same word
  three times in a few lines, sentences in a row that start the same way. These are patterns to
  look at, not errors: a phrase can be right where it is, and a dialogue-heavy scene can be fine.
- **Review** has the persona read the chapter (or only the selection) as an editor, with the
  story memory and what Check found, and write notes: the passage, what kind of problem (slop,
  cliché, repetition, continuity, error, dialogue, pacing, underdeveloped), why, and sometimes a
  minimal alternative. Its rules are in the **Review** template, not in the persona: in the
  Talk tab the persona stays your writing partner. A chapter may get no notes at all. A big
  model judges better; it takes a few minutes per chapter.
- Notes show as a soft wavy underline in the text and as a list. Click one to go to its
  passage. **Try it** puts its alternative into the text as changes (red and green, to accept or
  decline); **Ask…** takes it into the talk; **Done**, **Dismiss** (not a problem here; a new
  Check doesn't bring it back), **Reopen**.
- Notes are for the session: they stay while AIcove is open (also when you switch chapters)
  and are gone when you close it. Run Check or Review again on the text as it is then.
- Notes stay on their passage while you edit around and in them. When a passage is changed so
  much that the note no longer fits, the note becomes **outdated** and is no longer drawn in the
  text, instead of pointing at the wrong words.

**Story memory…** (the bible): a model can't read a whole novel at every question, so it reads
each chapter once and keeps notes, and those go along with everything you ask instead.

- **Build / update** goes through the chapters in order: it reads one, writes its note, brings
  the **Cast** (only the characters in that chapter) and the **Threads** up to date, and goes
  on to the next. For each chapter it gets the summaries of the chapters before and the cast so
  far, not the chapters themselves. Chapters that already have a current note are skipped. It
  runs in the background; Stop, or closing the window, ends it, and the next build goes on from
  there. **Make cast and threads again** makes both anew from all the notes.
- **Read with**: the model that does the reading (remembered per project). A small one is
  enough and much quicker: about a minute a chapter with a 9B model, three with a 27B one. It is
  loaded for the build; your persona's own model comes back when you next ask it something. The
  reader works without a persona voice: a plain archivist that records what happens, nothing
  softened or left out.
- **One chapter at a time**: **Remember** under the chapter list reads the selected chapter
  (its note, then cast and threads); so does **Read this chapter** in the Story memory window.
  Models only work when you ask: marking a chapter **Done** reads nothing, it only tells you
  when the chapter's memory is out of date. Working through a story in order, Remember each
  chapter when you are through with it, and the memory knows the chapters before the one you
  are on. The chapter list shows "not in memory" and "memory outdated".
- **Correct it**: open a note or page, fix what is wrong, Save. What you changed is locked (a
  whole chapter note, or the sections of Cast and Threads, one per `## ` heading) and building
  never overwrites it; **Unlock** gives it back to the model.
- **Style (yours)**: what the writing is going for: genre and tone, who it is for, pace, how
  much dialogue and description, the voice, what to avoid. It goes along with the talk, Review
  and the templates that rewrite, so the model judges and writes by your aim instead of its own
  taste; not with English polish (which only fixes) or the memory reader (which only records).
- **Plans (yours)**: where the story is going, what must not be forgotten. Only you write it;
  the models read it as plans, not as what happened.
- What a model gets with every question: your style and plans, the cast, the threads and the chapter
  notes (only their summaries, or only the chapters near the open one, when the model's context
  is small), next to the open chapter itself.

**The changes**: nothing a model writes is in your chapter until you accept it. Put the cursor
in a change or right-click it: **✓ Accept**, **✗ Decline**, **✎ Edit…** (change it, then
take it). The bar above the text has the same, ◀ ▶ to go from one to the next, and **Accept
all** / **Decline all**; it also says what the change at the cursor is ("“upon” → “on”",
"space → ¶"). A change of only spaces or line breaks, which can't be seen, marks its whole
line. Changes wait across restarts and chapter switches; the chapter list shows how many each
chapter has. You can type around them as usual.

Where it is: `~/AIcove/writing/<project>/`: `project.txt` (title, the chapters in order, draft
or done, the persona), `chapters/c001.md` …, `bible/` (the story memory: `chapters/` notes,
`cast.md`, `threads.md`, `style.md`, `plans.md`), `suggestions/` (the changes waiting),
`talk/` (the talks). The templates are in `~/AIcove/writing/templates/`,
the slop list is `~/AIcove/writing/slop.txt`. Files can be edited by hand too; a chapter file
put there joins at the end.

## Personas

A persona is a name, a picture, a prompt and a few settings, tied to the launchers it runs on.

- **Start** loads the persona's model (stopping whatever was loaded). The Chat tab does this by
  itself when you send.
- **Model**: the launcher this persona starts with. **Edit model settings…** jumps to it.
- **Also runs on**: other launchers it may use; **Choose…** ticks them. The Chat tab then offers
  them all at the top; prompt, knowledge, facts and chats stay the same on each.
- **Own copy…** gives the persona a launcher of its own, so changing the original no longer
  changes the persona. Without an own copy the launcher is shared, and the form says with whom.
- **Settings for this persona** (Thinking, Max answer length, temperature and so on) apply
  whenever the persona is used. Who wins: the persona, then the launcher, then llama-server's
  default.
- Give every persona a **Max answer length**. Without one, a model that starts repeating itself
  writes until the context is full.
- **Export…** saves a persona as one `.zip`: to keep, to move to another machine, or to give
  away. Its prompt, settings and picture always go along; tick what else should: knowledge
  files, facts (what it remembers about you), chats and its best of. Leave facts and chats out when you give
  it to someone. **Import…** adds a persona from such a file. If one with that name is already
  here, the new one gets "(2)" after its name. Its models are launcher names: if this machine
  has none of them, pick a model and Save.

## Launchers

A launcher is one model file together with its settings. The list on the left shows them;
**▶** marks the one that is loaded, **⇣** one that is still downloading.

**The form**

- A ticked box means the option is used. Unticked means llama-server's own default.
- **On the GPU**: *all* puts the whole model on the graphics card (it has to fit), *auto* puts
  on it what fits and runs the rest from normal RAM (slower), or give a number of layers.
  Not set means the default from the Settings tab.
- **Experts in normal RAM**: for mixture-of-experts models (Qwen3.6-35B-A3B and the like).
  The experts, most of the file, stay in normal RAM and only the small part that is always
  used goes on the graphics card. That lets a model several times the size of the card run
  at a good speed.
- **Memory** estimates what the launcher needs against what the graphics card has. Orange
  means tight or only partly on the graphics card, red means too big.
- **Writing style** buttons (Model defaults, Factual, Creative, Story) set several options at
  once. They are only defaults; a persona can set its own.
- **Extra options**: anything the form doesn't know, typed as on a command line.
- **What will run** shows the full command, updated while you change things.

**Buttons**

- **Save** / **Revert**: changes only count after Save.
- **Save & Start** saves, stops the model that was loaded and loads this one.
- **Stop llama-server** stops only the model.
- **Add model…** brings in a new model: from a Hugging Face link (it lists the versions and
  marks the one that fits this machine) or from a file that is already on the disk. Downloads
  run on their own and continue where they stopped, also after closing AIcove.
- **New from this one…** copies the form into a new launcher.
- **New persona on this one…** makes a persona with its own copy of this launcher.
- **From another machine…** adds the launchers of another computer of yours (see below).
- **Update from other machines** asks those computers what they have now.
- **Rename…**, **Delete**: Delete removes the launcher (a copy is kept). If no other launcher
  uses its model file, the box **Also delete the model file from the disk** offers to delete the
  file too, with its eyes file and a half-finished download. Not while that model is loaded:
  stop it first.

Useful options: **Context size** is how much of the chat the model remembers (bigger needs
more memory). **Parallel chats** set to 1 gives the whole context to one chat. **Compress
context (K)** and **(V)** roughly halve the context's memory for a small loss in quality.
**Max answer length** stops an answer that would otherwise run on.

**Thinking budget** caps how much a thinking model thinks before it answers (in tokens; it
counts against the answer's length too). **When the budget is used up** is a sentence the model
is told at that moment, so it moves on to answering cleanly. Both apply to every persona on
that launcher; they take effect when the model is started again.

**Models on another machine**

A second computer with AIcove (say one with a faster graphics card) can run the model while
the chat stays here: personas, chats, files, pictures and voice remain on this machine, only
the chat text goes back and forth. You need an ssh key that logs in to that machine without a
password, and a name for it in `~/.ssh/config` (a `Host` line). **From another machine…** asks
for that name and adds all its launchers, as *machine-name*. On this tab they are listed under
a heading with the machine's name, below this machine's own; elsewhere they carry **🖥** and the
machine's name, and the status says *on* that machine. They follow that
machine: AIcove asks it when the window opens, when one of its launchers is started, and on
**Update from other machines**. Then a new launcher there appears here, one deleted there goes
here too, and a changed name or model shows. One you
delete here stays away until you add the machine again. **Start** starts the model over there with that machine's own settings and
opens an ssh tunnel, so to the chat it looks like a model here. **Stop llama-server** stops it
there and closes the tunnel. The settings of such a launcher are changed on the machine it
belongs to. Its model stays private there: nothing is opened to the network.

By hand, a launcher file with two lines does the same: `host: <name from ~/.ssh/config>` and
`launcher: <its launcher there>`.

## Letting a model use your files

A persona can be allowed to read files of this machine and to write text files. It can never
run commands: when something has to be run or changed in the system, it explains and gives
you the command, and you run it.

**Switching it on**

1. **Settings** tab, *File access for the AIs*: the folders it **may read**, the folders it
   **may also write in**, and whether it may read **system files** (/etc, /var/log, /proc and
   the like, so it can look at the machine and explain).
2. **Personas** tab, **Files**: per persona *No access*, *May read*, or *May read, and write
   after asking you each time*. Save; it counts from the next message.

Without both, a persona has no access.

**Each persona's own folder**

Every persona has a folder in your AIcove folder, `~/AIcove/personas/<persona>/`. Two parts
of it are for the persona itself, when it has file access:

- `knowledge/`: material this persona works from. It is used in every chat even without file
  access (see *Knowledge* in the topic *Chat*); with file access the persona can also read it
  with its file tools.
- `notes/`: where it writes down what it should still know in a later chat. That is its
  memory between chats; you can read, change and delete those files like any others.

**Its own folder…** next to *Files* on the Personas tab opens it. Other personas and the plain
model can't read it, and the persona itself can't touch the rest of its folder (its persona
file, its chats). Writing there needs *May read, and write* and your Confirm each time,
like every write. It reads plain text (txt, md, csv, code); not PDF or office files.
A persona doesn't look into its folder by itself in every chat: say "check your notes", or
put a line into its prompt such as "At the start of a chat, look into your own folder."

**It knows this guide**

A persona with file access can also read this guide and your notes file (the *My notes*
topic), topic by topic, without any folder being allowed for that. So you can ask it how
something on this machine works or what to do, and it looks it up first. Keep the notes file
free of passwords.

**What happens when it writes**

A window opens: which file, how many lines change, and the change itself. Nothing is written
until you press **Write it**; **Don't** (the default) tells the persona no. The old version of a changed file is kept in
`~/.cache/aicove/access-backups/`.

**What it can never do, whatever the settings say**

- Read keys and passwords: the password store, SSH and GnuPG keys, browser profiles, and any
  file whose name says it holds secrets (`*password*`, `*.key`, `.env`, `shadow`, ...).
- Write outside the folders you allowed, into hidden files or folders, or over a file that is
  a program. Those would be ways to run something later.
- Change system files, or delete or move anything.

Everything it reads, searches or writes is listed in `~/.cache/aicove/access.log` (which file,
when, which persona; never the contents).

**Good to know**

- Use a model that is good with tools. Coding models and the larger general ones do this
  well; small creative models often don't call the tools at all, or call them wrongly.
- What a model has read is in the chat from then on. If web search is switched on in that
  chat, a model could put text from a file into a search. Don't combine web search with
  access to things you'd rather keep on this machine.
- A model can be wrong about what it read. For anything that matters, look at the file.

## Tools

The Tools tab holds everything else you start and stop on this machine: one row per tool
with its state, **Start**, **Stop**, **Open** (in the browser), buttons of its own, and
**Edit…**. The top bar shows which tools are running.

**Add tool…** asks for:

- **Name** and **Short name** (the file name; also the name for a terminal).
- **Start command** and **Stop command**: shell commands.
  Leave Stop empty when the start command *is* the program: AIcove then keeps it running in
  the background and ends it on Stop. Fill in both for things that have their own start and
  stop commands.
- **Folder**: where the commands run.
- **Address**: gives the Open button, and tells whether the tool runs (something answers there).
- **Start and stop in a terminal window**: for commands that ask for a password or a yes/no.
- **"Stop everything" stops it too**: off by default.
- **Not together with**: tools that are stopped before this one starts.
- **Never on together with a loaded model**: for tools that need the graphics card's memory
  themselves, such as a voice tool. Starting the tool stops the model, and loading a model
  stops the tool.
- **Extra buttons**: one per line, `Label | command`. They always run in a terminal window.

A tool that asks for a password (su, sudo) must have the terminal box ticked, or it has
nowhere to ask.

## Keyboard

The **Keyboard** buttons in the top bar switch the keyboard layout. The active one is marked,
also when the layout was switched outside AIcove (the mark follows within a few seconds).

**Setting them up**: Settings tab, group *Keyboard*.

- **Keyboard buttons**: one per line, `Label | command`. Any command that switches the
  layout works, for example `Game | setxkbmap -layout us -variant game`.
- **Which one is active**: optional, a command that prints the active keyboard. The button
  whose label is in its answer is marked. Without it, the last button pressed is marked.

A machine with no lines there has no keyboard buttons. The lines belong to one machine,
because layouts and commands differ.

## Passwords

A small password manager. Each entry is one file, encrypted with GnuPG. You have one master
password; GnuPG asks for it in its own window and remembers it for a while. AIcove never
sees it. Nobody can reset it: without it the entries can't be read.

**Using it**

- Click an entry to open it. **Copy** next to the password copies it, and so does a
  double-click on the entry. The clipboard is emptied again after 45 seconds.
- **New…**: name, user, password, address, notes, then Save. A `/` in the name makes a group,
  for example `server/database`.
- **Generate** makes a random password of 20 characters.
- **Lock** makes GnuPG forget the master password now.
- Every change is kept in the store's history, so an overwritten or deleted entry can be
  recovered.

**A second machine**

1. First machine: **More → Set sync address** (a git address you can push to), then **Sync now**.
2. First machine: **More → Export key**. Carry that file over yourself.
3. Second machine: **Get my store from another machine…**, then **More → Import key**. Delete
   the key file on both.

After that **Sync now** on either machine exchanges the changes. If the same entry was changed
on both before syncing, nothing is merged until you pick a side in the More menu.

The server only ever holds encrypted entries. The key is not put there.

## Settings

Everything that differs from one machine to the next: the llama-server program, your AIcove
folder, the model folder, how much memory the graphics card has, how much of a model
goes on it by default, the ports, the terminal program for tools, the keyboard buttons, what the AIs may read and
write, the password store and the notes file.

- **Find again** looks the machine over and fills in what it finds.
- **Check** tries every entry and says what is wrong. It checks what is in the form, saved or not.
- **Save** writes the settings.

On a machine where AIcove runs for the first time, it opens on this tab with what it found.

**Keyboard buttons**: the buttons in the top bar that switch the keyboard layout. See the
topic *Keyboard*.

**Model reachable from**: "this machine only" is the safe choice. "Everyone on the network"
lets other devices talk to the loaded model directly, without a password. Takes effect when the
model is next started.

## From a terminal

The window does all of this; these are the same actions as commands.

| Command | Does |
|---|---|
| `aicove` | open the window |
| `aicove start NAME` | load a launcher's model, or a persona on its model |
| `aicove stop` | stop the model |
| `aicove stop all` | stop the model and the tools marked for it |
| `aicove status` | what is running |
| `aicove list` | launchers and personas |
| `aicove info` | this machine's launchers as JSON (what another machine asks for) |
| `aicove chat PERSONA` | that persona's chats |
| `aicove agent` | the agents and how they last ran |
| `aicove agent run ID` | run one now (`aicove agent help` for the rest) |
| `aicove chat PERSONA "message"` | a new chat (loads the persona's model when needed) |
| `aicove chat PERSONA last "message"` | go on with its latest chat (or give a chat's id) |
| `aicove tools` | the tools and whether they run |
| `aicove tools start NAME` / `stop NAME` | one tool |
| `aicove passwords` | names of the password entries (never the passwords) |
| `aicove passwords sync` | sync the password store |
| `aicove check` | the same as Check on the Settings tab |

## Where things are

Everything is a plain text file and can be edited by hand.

| What | Where |
|---|---|
| Settings of this machine | `~/.config/aicove/config.toml` |
| Launchers | `~/.config/aicove/launchers/` |
| Your AIcove folder: personas (each a folder with persona.txt, picture, knowledge, notes), agents and their reports, writing projects, documents, work | `~/AIcove/` |
| Tools | `~/.config/aicove/tools/` |
| Passwords | `~/.password-store/` |
| Logs, downloads in progress, old versions of saved launchers and tools | `~/.cache/aicove/` |
| What the AIs did with files; old versions of files they changed | `~/.cache/aicove/access.log`, `~/.cache/aicove/access-backups/` |
| The program | the folder you cloned; `git pull` there updates it |

To take everything that is yours to another machine, copy `~/AIcove/`. Launchers and tools
belong to one machine, because the model files and programs differ. A persona names the
launchers it runs on (`models:` in its file); on a machine without them, pick another.

**Documents and work.** `~/AIcove/documents/` is for material every persona with file access
may read; `~/AIcove/work/` is where they may write (each change after your OK). Both are
ordinary entries of the file access settings and can be changed there.

## When something doesn't work

- **A model doesn't load**: the pane at the bottom shows the last lines of llama-server's log.
  "Too big" in the Memory line means it doesn't fit the graphics card: lower **Context size**,
  tick **Compress context**, or set **On the GPU** to *auto*.
- **"No answer." under the chat**: llama-server stopped while answering. The note says why,
  from its log. A picture can be more than the model takes in one batch (Gemma models crash on
  that): models with eyes get a batch of 2048 by themselves; for bigger pictures add
  `-b 4096 -ub 4096` to the launcher's options. This is a model setting (Launchers tab), not a
  persona one: it is fixed when llama-server starts.
- **A tool's terminal says "must be run from a terminal" or can't ask for a password**: tick
  *Start and stop in a terminal window* under Edit….
- **A persona doesn't use the files**: check that it has **Files** set (and saved) on the
  Personas tab, and that the folder is allowed on the Settings tab. Its model has to know tools:
  the Launchers tab says *can use files* or *can't use files* next to the file size, and the
  Personas tab warns when a persona with Files runs on a model that can't. If the model
  answers "I would read…" without doing it, it is not good with tools; try another one.
- **A keyboard button does nothing**: the pane at the bottom shows what its command answered.
  "Error loading new keyboard description" means the system doesn't have that layout or
  variant; see the topic *Keyboard*.
- **Passwords: "The key of this store is not on this machine"**: More → Import key, with the
  key file exported on the machine that has it.
- **Anything about paths or programs**: Settings tab, **Check**.
