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
instead of them; the newest part stays word for word. The chat on screen stays complete:

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
SearXNG if the `web_search` setting (Settings tab) is its address, e.g. `http://127.0.0.1:8888`.
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
- **Rename…**, **Delete**: Delete removes the launcher (a copy is kept). If no other launcher
  uses its model file, the box **Also delete the model file from the disk** offers to delete the
  file too, with its eyes file and a half-finished download. Not while that model is loaded:
  stop it first.

Useful options: **Context size** is how much of the chat the model remembers (bigger needs
more memory). **Parallel chats** set to 1 gives the whole context to one chat. **Compress
context (K)** and **(V)** roughly halve the context's memory for a small loss in quality.
**Max answer length** stops an answer that would otherwise run on.

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
| `aicove chat PERSONA` | that persona's chats |
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
| Your AIcove folder: personas (each a folder with persona.txt, picture, knowledge, notes), documents, work | `~/AIcove/` |
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
