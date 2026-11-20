// Every word the window shows, one table per language. Components read the current table with s();
// the app switches it with setLanguage() and re-renders.
import type { Job, JobProgress, MachineInfo, Preset, PresetInfo, Status, Violation } from "./api/contract.gen";

type PausedKind = NonNullable<Status["paused_kind"]>;

export type Lang = "en" | "cs";

const num = (n: number) => String(Math.round(n * 10) / 10);
const dec = (n: number) => num(n).replace(".", ","); // Czech decimal comma

/** Czech plural: 1 / 2-4 / other. */
const cz = (n: number, one: string, few: string, many: string) => (n === 1 ? one : n >= 2 && n <= 4 ? few : many);

const en = {
  appName: "TalkSubs",
  nav: { queue: "Queue", settings: "Settings", help: "Help", pages: "Pages" },
  lang: { label: "Language" },
  look: { label: "Look", light: "Light look", dark: "Dark look" },
  info: { more: "More information", close: "Close" },
  conn: { lost: "Lost the connection to the app. Reconnecting…" },
  paused: (reason: string, kind: PausedKind | null): string =>
    kind === "gpu_memory"
      ? "Waiting: the graphics card has no memory left; another program is probably using it. " +
        "The queue tries again in a few minutes and goes on where it stopped."
      : kind === "disk_full"
        ? "Waiting: the disk is full, so a finished transcript cannot be saved. Free some space on the drive that " +
          "holds the app; the queue tries again in a few minutes and goes on where it stopped."
        : `Waiting: ${reason}`,
  models: {
    title: "First start: downloading the speech model",
    note:
      "The app downloads its speech model once. After that it works without the internet. Lectures you add now " +
      "wait until it is in.",
    amount: (done: string, total: string): string => `${done} of ${total}`,
    failedTitle: "The speech model is not downloaded yet",
    retry: "Try again",
    label: "Speech model download",
    error: {
      no_connection: () =>
        "There is no connection to the internet, or huggingface.co cannot be reached. The model is downloaded once, " +
        "on the first start. Connect to the internet and press Try again.",
      dropped: () => "The connection dropped. Press Try again: the download goes on where it stopped.",
      secure: () =>
        "A secure connection to huggingface.co could not be set up (a firewall or a company proxy may be in the " +
        "way). Press Try again, or try on another network.",
      site: () => "The model site answered with an error. Press Try again later.",
      disk_full: () => "The disk became full. Free some space and press Try again: it goes on where it stopped.",
      no_space: (need: string, free: string) =>
        `There is not enough free disk space: the model needs ${need}, the drive that holds the app has ${free} free. ` +
        "Free some space and press Try again.",
      damaged: () => "A downloaded part of the model is damaged. Press Try again.",
    },
  },
  card: {
    label: "Graphics-card support",
    offer: (name: string): string => `This PC has an NVIDIA graphics card (${name}) that the app can use.`,
    note: (size: string): string =>
      `Download its support once (${size}, NVIDIA's official files) and transcription runs on the card, about twice ` +
      "as fast (more on a PC with a slower processor). Until then the app keeps working on the processor.",
    download: (size: string): string => `Download the graphics-card support (${size})`,
    tip: "Downloads NVIDIA's files the app needs to use the graphics card; once, then it works offline again.",
    downloading: "Downloading the graphics-card support",
    goingNote:
      "Transcriptions go on meanwhile, on the processor. Closing the app pauses the download; it goes on at the next start.",
    unpacking: "unpacking…",
    failedTitle: "The graphics-card support is not downloaded yet",
    error: {
      no_connection: () =>
        "There is no connection to the internet, or NVIDIA's files cannot be reached (files.pythonhosted.org). " +
        "Connect to the internet and press Try again.",
      dropped: () => "The connection dropped. Press Try again: the download goes on where it stopped.",
      secure: () =>
        "A secure connection to files.pythonhosted.org could not be set up (a firewall or a company proxy may be in " +
        "the way). Press Try again, or try on another network.",
      site: () => "The download site answered with an error. Press Try again later.",
      disk_full: () => "The disk became full. Free some space and press Try again: it goes on where it stopped.",
      no_space: (need: string, free: string) =>
        `There is not enough free disk space: the support needs ${need}, the drive that holds the app has ${free} ` +
        "free. Free some space and press Try again.",
      damaged: () => "A downloaded part is damaged. Press Try again.",
    },
  },
  locked: {
    text:
      "The work kept in this app cannot be opened here: it was locked by another Windows account or on another " +
      "computer, its key is missing, or it is damaged. Subtitle files you saved are not affected.",
    startFresh: "Start fresh",
    confirm:
      "Start with an empty app? The old work is not deleted: it is moved into a folder named “old-…” " +
      "in the app's data folder.",
  },

  drop: {
    title: "Add lectures",
    hint: "Drop audio or video files here, or",
    button: "Choose files…",
    active: "Drop to add",
    copyNote:
      "Dropped files are copied into the app first (of a video, only its sound); files chosen with the button are " +
      "used where they are.",
    choosing: "Choose files in the Windows window…",
    info:
      "“Choose files…” uses the files where they are: nothing is copied and no extra disk space is used. " +
      "A dropped file is copied into the app first, because the window cannot see where it lies; " +
      "the copy is deleted when you remove the item from the list.",
  },
  link: {
    label: "Or paste a link to a recording",
    placeholder: "https://…",
    add: "Add link",
    adding: "Adding…",
    note: "Only the sound is downloaded and kept, locked; the picture is never saved.",
  },
  upload: {
    waiting: "Waiting to copy",
    sending: (done: string, total: string): string => `Copying ${done} of ${total}`,
    checking: "Checking the file…",
    failed: "Not added",
    notMedia: (name: string): string => `${name} is not an audio or video file.`,
    unreadable: (name: string): string => `${name} could not be read as audio or video.`,
    noSpace: "There is not enough free disk space for a copy of this file.",
    noRoom: (size: string, free: string, spare: string): string =>
      `There is not enough free disk space for a copy of this file: it takes ${size}, TalkSubs keeps ${spare} free ` +
      `for its own work, and only ${free} is free now. Free some space on that drive and add the file again.`,
    cancel: "Cancel",
    dismiss: "Dismiss",
    list: "Copying",
  },

  queue: {
    title: "Queue",
    empty: "Nothing here yet. Add a lecture above; its subtitles will be ready to save when it is done.",
    clearHint: "Finished items stay until you remove them.",
  },
  state: {
    fetching: "Downloading",
    queued: "Waiting",
    running: "Transcribing",
    done: "Done",
    failed: "Failed",
    cancelled: "Cancelled",
  } satisfies Record<Job["state"], string>,
  phase: {
    fetch: "Downloading the sound",
    load: "Loading the speech model",
    decode: "Reading the audio",
    speech: "Finding speech",
    recognise: "Recognising speech",
    done: "Finishing",
  } satisfies Record<JobProgress["phase"], string>,
  progress: {
    position: (done: string, total: string): string => `${done} of ${total}`,
    eta: (left: string): string => `about ${left} left`,
    etaSoon: "less than a minute left",
    etaUnknown: "estimating time left…",
  },
  job: {
    cancel: "Cancel",
    cancelling: "Cancelling…",
    remove: "Remove",
    removeAsk: "Remove this lecture from the list?",
    removeDeleted: "Deleted for good:",
    removeKept: "Kept:",
    removeWhat: {
      file: {
        deleted: "the transcript, your edits in the editor and the listening copy.",
        kept: "your original file, where it is, and every subtitle file you saved.",
      },
      upload: {
        deleted: "the copy the app made when you dropped the file, the transcript, your edits and the listening copy.",
        kept: "your original file, wherever you dropped it from, and every subtitle file you saved.",
      },
      link: {
        deleted: "the sound downloaded from the link, the transcript, your edits and the listening copy.",
        kept: "every subtitle file you saved. To work on this lecture again, paste its link again.",
      },
    },
    removeConfirm: "Remove and delete",
    removeKeep: "Keep it",
    attempts: (n: number): string => `Started ${n} times`,
    estimate: (t: string): string => `takes about ${t}`,
    estimateSoon: "takes less than a minute",
  },
  preview: {
    waiting: "Preview waiting to be prepared",
    making: (pct: number): string => `Preparing the preview… ${pct} %`,
    failed: (why: string): string => `The preview could not be made: ${why}`,
    show: "Show preview",
    hide: "Hide preview",
    videoLabel: "Preview of the lecture video",
    audioLabel: "Preview of the lecture audio",
  },

  types: {
    title: "Subtitle types",
    info:
      "A subtitle type is a set of rules: how long a line may be, how fast it may be read, how long a subtitle stays " +
      "on screen. Besides the ones that come with the app you can make your own, starting from a published standard " +
      "or from a copy, and change its values.",
    builtin: "(comes with the app)",
    orderInfo:
      "The order here is the order everywhere: in the queue, in the editor and in the result. Drag a row by its grip or use the arrows, then save.",
    dragTip: "Drag to move this type up or down the list",
    up: "Move up",
    down: "Move down",
    copy: "Make a copy",
    copyTip: "Make your own type from this one, then change its values.",
    copyName: (name: string): string => `${name} (my copy)`,
    edit: "Change",
    editTip: "Show and change the values of this type.",
    close: "Close",
    remove: "Remove",
    removeTip: "Remove this type. Subtitles already made with it stay; it can be put back.",
    removeAsk: (name: string): string =>
      `Remove the type “${name}”? Subtitles already made with it stay and can still be opened and saved; ` +
      "the type is kept aside and can be put back.",
    fromStandard: "New type from a standard:",
    standardTip: "Published subtitling standards to start from.",
    create: "Create",
    createTip: "Make your own type with this standard's values, then change them as you like.",
    full: "You have 20 own types, the most there can be. Remove one to make another.",
    retired: (n: number): string => `Removed types (${n})`,
    retiredNote: "Subtitles made with these types can still be opened and saved. Put a type back to use it again.",
    restore: "Put back",
    forget: "Delete for good",
    retiredFull: "There are 50 removed types, the most there can be. Delete one of them for good first.",
    forgetTip: "Delete this removed type for good.",
    forgetAsk: (name: string): string =>
      `Delete the type “${name}” for good? Subtitles already made with it stay in the lectures, but they can no ` +
      "longer be chosen by this type to open or save.",
    dropAsk: (name: string): string => `Remove the type “${name}”? It has not been saved yet, so it is simply dropped.`,
    restoreTip: "Make this type available again.",
    saveNote: (button: string): string =>
      `Changes to the types are kept when you press “${button}” at the bottom of the page.`,
    basedOn: (name: string): string => `Based on: ${name}.`,
    name: "Name",
    description: "Description",
    groupText: "Text",
    chars: "Characters per line",
    charsTip: "The longest a line may be. Most standards use 42 (television 37).",
    lines: "Lines per subtitle",
    linesTip: "One or two lines.",
    cps: "Reading speed (characters per second)",
    cpsTip:
      "How fast a subtitle may be read. Lower is easier: 12-15 is comfortable, 17 is the usual maximum for Czech, 20 for English.",
    spaces: "Spaces count in the reading speed",
    spacesTip: "Netflix and the Czech norm count spaces; leave it on unless your standard says otherwise.",
    groupTime: "Timing",
    minDur: "Shortest time on screen (s)",
    minDurTip: "A subtitle stays at least this long, so it can be noticed.",
    maxDur: "Longest time on screen (s)",
    maxDurTip: "A longer stretch of speech is split into more subtitles.",
    gap: "Gap between subtitles (frames)",
    gapTip: "The shortest pause between two subtitles, so the eye notices a new one. Usually 2 frames.",
    closeGaps: "Close gaps up to (frames)",
    closeGapsTip:
      "A gap this short or shorter is closed down to the minimum gap, so subtitles follow each other smoothly. 0 switches it off.",
    leadIn: "Show before the speech (ms)",
    leadInTip: "A subtitle may appear this long before its first word. Usually 0.",
    hold: "Stay after the speech (ms)",
    holdTip: "The longest a subtitle stays after its last word, to give time to read it.",
    grid: "Frame rate for sound-only recordings",
    gridTip: "Subtitle times are placed on frames. A video keeps its own frame rate; this is for sound only.",
    fps: (g: string): string =>
      ({
        "24000/1001": "23.976 (film)",
        "24/1": "24",
        "25/1": "25 (Europe)",
        "30000/1001": "29.97 (USA)",
        "30/1": "30",
      })[g] ?? g,
    groupWords: "Words",
    fillers: "Leave out “uh”, “um” and similar sounds",
    fillersTip: "The transcript keeps them; only the subtitles leave them out.",
    capitals: "No capital in mid-sentence after a pause",
    capitalsTip:
      "The speech model writes the first word after a pause with a capital; this takes it back where no sentence begins.",
    groupSeverity: "How strict each rule is",
    severityInfo:
      "Must fix: shown in red, the editor counts it as a mistake. Worth a look: shown as a hint you may leave as it is.",
    hard: "Must fix",
    soft: "Worth a look",
    rule: {
      chars_per_line: "Characters per line",
      lines: "Number of lines",
      cps: "Reading speed",
      min_duration: "Shortest time on screen",
      max_duration: "Longest time on screen",
      min_gap: "Gap between subtitles",
      break_quality: "Good line breaks",
    } as Record<string, string>,
  },
  tips: {
    choose:
      "Pick audio or video files on this computer. They stay where they are; the app keeps only its own encrypted " +
      "copy of the sound for listening.",
    addLink: "Download only the sound from this link and add it to the queue. The sound is kept encrypted.",
    cancelUpload: "Stop copying this file into the app.",
    preview: "Listen to the lecture here (sound only).",
    cancelJob: "Stop this transcription. Lectures that are already done are not affected.",
    remove: "Remove from the list. It asks first and says what is deleted.",
    removeConfirm: "Delete the app's own files for this lecture for good. Saved subtitles stay.",
    removeKeep: "Close this question and keep the lecture.",
    save:
      "Save the ticked kinds of subtitles in the ticked file types, where Settings says: beside the original file " +
      "or in one folder.",
    edit: "Open the editor: check and correct the subtitles while you listen.",
    openFolder: "Open the folder with the saved subtitles in Windows.",
    change: "Choose which kinds of subtitles and which file types are saved.",
    replace: "Save anyway and replace the existing files.",
    keepFiles: "Do not save; the existing files stay as they are.",
    measure: "Measure how fast this PC transcribes, for the time estimates.",
    retryModel: "Try the download of the speech model again.",
    chooseFolder: "Choose the folder where saved subtitles go.",
    resetFolder: "Go back to the app's own subtitles folder.",
    saveSettings: "Save these settings.",
    look: "Switch between the light and the dark look.",
    lang: "Language of the window.",
    startFresh: "Start with an empty store; the old one is kept aside, nothing is deleted.",
    preset: "Which kind of subtitles you are editing.",
    reopen: "Load the subtitles again as they are saved now.",
    undo: "Undo the last change (Ctrl+Z).",
    redo: "Redo what was undone (Ctrl+Y).",
    startOver: "Make these subtitles again from the recording with today's rules. Undo brings the old ones back.",
    controls: "How to control the editor with the keyboard and the mouse.",
    split: "Split this subtitle in two at the text cursor (Ctrl+Enter).",
    merge: "Join this subtitle with the next one (Ctrl+J).",
    insert: "Add a new subtitle at the play position (Ctrl+I).",
    remove1: "Delete this subtitle (Delete key).",
    play: "Play or pause (Space).",
    nextProblem: "Go to the next subtitle that breaks a rule.",
    time: "Type a time (for example 1:02,5 or 0:01:02.500) and press Enter, or use − and + to move by one frame.",
    translation: "The translation of this subtitle; it is saved by itself, like the English text.",
    translationLanguage: "The language code of the translation (cs, sk, de …); it names the translated files.",
    startOverTranslated: "These subtitles have a translation, so they cannot be made again from the recording.",
    texts:
      "Save the English subtitles, the translation, or both. Untranslated subtitles are left out of a translation.",
    showTranslation: "Show or hide the translation column and box (for translating). Remembered on this computer.",
    showTranslationLocked: "These subtitles have a translation, so it is always shown.",
  },
  editor: {
    open: "Edit subtitles",
    back: "Back to the queue",
    title: (name: string): string => `Subtitles: ${name}`,
    loading: "Opening the subtitles…",
    gone: "This lecture is no longer in the queue.",
    preset: "Subtitle type",
    saved: "All changes saved",
    pending: "Not saved yet",
    saving: "Saving…",
    failed: (why: string): string => `Not saved: ${why} Trying again…`,
    staleShort: "These subtitles were changed elsewhere. Open them again.",
    reopen: "Open again",
    tools: "Editing tools",
    undo: "↶ Undo",
    redo: "↷ Redo",
    startOver: "Remake from the recording…",
    startOverAsk:
      "Make these subtitles again from the recording, with the current rules? Your changes to them are replaced. Undo brings them back.",
    presetChanged:
      "The rules of these subtitles have changed since they were edited. They keep the rules they were made with; “Remake from the recording” uses the new ones.",
    noPreview: "The waveform and the player appear when the preview is ready.",
    previewFailed:
      "There is no preview of this lecture, so there is no waveform or player. The text can still be edited.",
    waveform: "Waveform with the subtitles",
    dragStart: "Drag to move the start",
    dragEnd: "Drag to move the end",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    play: "Play",
    translation: "Translation",
    translationLanguage: "Translation language",
    translationPlaceholder: "Type the translation of this subtitle here (one subtitle line per text line).",
    translationStats: (longest: number, maxChars: number, rate: number, maxCps: number): string =>
      `longest line ${longest} / ${maxChars} characters · ${num(rate)} / ${num(maxCps)} characters per second`,
    translationColumn: "Translation",
    showTranslation: "Show translation",
    pause: "Pause",
    now: "Current position in the recording",
    stop: "■ Stop",
    stopTitle: "Stop and go back to the start of the recording",
    jumpBack: "« 10 s",
    jumpBackTitle: "10 seconds back (← key)",
    jumpForward: "10 s »",
    jumpForwardTitle: "10 seconds forward (→ key)",
    list: "Subtitle list",
    count: (n: number): string => `${n} subtitle${n === 1 ? "" : "s"}`,
    none: "Choose a subtitle in the list or on the waveform.",
    text: "Text of the selected subtitle",
    textHint: "One subtitle line per text line.",
    textLabel: "Text (English)",
    cueOf: (n: number, of: number): string => `Subtitle ${n} of ${of}`,
    zoom: "Zoom",
    volume: "Volume",
    start: "Start",
    end: "End",
    length: "Length",
    speed: "Reading speed",
    cps: (n: number): string => `${num(n)} characters/s`,
    seconds: (ms: number): string => `${num(ms / 1000)} s`,
    earlier: "1 frame earlier",
    later: "1 frame later",
    split: "Split at the cursor",
    merge: "Join with the next",
    insert: "New subtitle",
    remove: "Delete",
    problems: (hard: number, soft: number): string =>
      hard + soft === 0 ? "No rule is broken." : `Must fix: ${hard} · Worth a look: ${soft}`,
    checking: "Checking…",
    nextProblem: "Next problem",
    noProblem: "Nothing to fix in this subtitle.",
    hard: "Must fix",
    soft: "Worth a look",
    keys: "Controls",
    keyList: [
      ["Space", "Play or pause (outside the text box)"],
      ["←  →  (outside the text box)", "10 seconds back / forward"],
      ["Esc / Enter", "Leave the text box / go into it"],
      ["↑ ↓  (Alt+↑ ↓ in the text box)", "Previous / next subtitle"],
      ["Ctrl+Z, Ctrl+Y", "Undo, redo"],
      ["Ctrl+Enter", "Split at the cursor"],
      ["Ctrl+J", "Join with the next subtitle"],
      ["Ctrl+I", "New subtitle at the play position"],
      ["Delete  (outside the text box)", "Delete the subtitle"],
      ["[  ]  (Alt+[ ] in the text box)", "Start 1 frame earlier / later"],
      ["Shift+[  ]", "End 1 frame earlier / later"],
      ["+  −", "Zoom the waveform in / out"],
      ["Ctrl+S", "Save now"],
      ["Click a subtitle", "Choose it (in the list or on the waveform)"],
      ["Double-click a subtitle on the waveform", "Play from its start"],
      ["Drag a subtitle's edge", "Move its start or end"],
      ["Drag a subtitle", "Move it whole, keeping its length"],
      ["Click an empty spot of the waveform", "Move the play position there (between subtitles)"],
    ] as [string, string][],
    rule: {
      order: () => "Starts before the subtitle above it.",
      overlap: (v: number, l: number) => `Overlaps the subtitle above by ${num((l - v) / 1000)} s.`,
      off_grid: () => "A time is not on a video frame.",
      empty: () => "An empty line or no text.",
      past_media_end: () => "Ends after the end of the recording.",
      chars_per_line: (v: number, l: number) => `A line has ${v} characters (at most ${l}).`,
      lines: (v: number, l: number) => `${v} lines (at most ${l}).`,
      max_duration: (v: number, l: number) => `Shown for ${num(v / 1000)} s (at most ${num(l / 1000)} s).`,
      min_duration: (v: number, l: number) => `Shown for only ${num(v / 1000)} s (at least ${num(l / 1000)} s).`,
      cps: (v: number, l: number) => `Reads at ${num(v)} characters/s (at most ${num(l)}).`,
      min_gap: (v: number, l: number) =>
        `Only ${v} frame${v === 1 ? "" : "s"} after the subtitle above (at least ${l}).`,
      break_quality: () => "Breaks at an awkward place (inside a name or after a small word).",
    } satisfies Record<Violation["rule"], (value: number, limit: number) => string>,
  },

  help: {
    guide: {
      title: "Step by step",
      intro: "Choose a topic; each opens a short guide.",
      topics: [
        {
          id: "start",
          title: "First start",
          steps: [
            "On the first start the speech model (2.5 GB) downloads once; the window shows its progress and the time left. If the download stops, press “Try again”: it goes on where it stopped.",
            "From then on the app works without the internet.",
            "On a PC with an NVIDIA graphics card the window offers to download its support once (1.7 GB, NVIDIA's official files); transcription then runs on the card, about twice as fast (more on a PC with a slower processor). Until then, and on any other PC, it runs on the processor.",
          ],
        },
        {
          id: "add",
          title: "Add a lecture",
          steps: [
            "On the Queue page press “Choose files…” and pick audio or video files. They stay where they are.",
            "Or drop files on the window: the app keeps its own encrypted copy of their sound.",
            "Or paste a link to a recording and press “Add link”: only the sound is downloaded and kept, encrypted.",
            "Lectures are transcribed one after another. Each shows its progress and the time left; “Cancel” stops it.",
            "A transcription interrupted by closing the app or a crash goes on where it stopped at the next start.",
          ],
        },
        {
          id: "listen",
          title: "Listen to a lecture",
          steps: ["On a lecture in the queue press “Show preview” to listen to it in the window."],
        },
        {
          id: "save",
          title: "Save subtitles",
          steps: [
            "On a finished lecture press “Change” to choose the kinds of subtitles, the file types and what to save (the original in English, the translation, or both).",
            "Press “Save subtitles”. The files go beside the original file, or into one folder, as Settings says (dropped files and links always go to the folder).",
            "If files of that name exist, the app asks whether to replace them (“Replace them”) or keep them.",
            "“Open folder” shows the saved files in Windows. Saving again with other choices needs no new transcription.",
          ],
        },
        {
          id: "edit",
          title: "Edit subtitles",
          steps: [
            "On a finished lecture press “Edit subtitles”; at the top choose which kind of subtitles you edit.",
            "Choose a subtitle in the list or on the waveform, and correct its text above (one subtitle line per text line).",
            "Move its start or end: drag its edge on the waveform, use − and + (one frame), or type the time and press Enter.",
            "“Split at the cursor”, “Join with the next”, “New subtitle” and “Delete” change the subtitles themselves. Drag a whole subtitle on the waveform to move it, keeping its length; ↑ and ↓ (Alt+↑ ↓ in the text box) go to the previous or next subtitle.",
            "Red marks show what must be fixed, yellow ones what is worth a look; “Next problem” goes to the next marked subtitle.",
            "Everything saves by itself; “Undo” and “Redo” (Ctrl+Z, Ctrl+Y) go back and forward.",
            "“Remake from the recording” makes the subtitles again with today’s rules (not while they have a translation).",
          ],
        },
        {
          id: "player",
          title: "Play and move around",
          steps: [
            "“Play” plays and pauses (Space); “Stop” stops and goes back to the start.",
            "“« 10 s” and “10 s »” jump ten seconds back or forward (← and → keys).",
            "The large time next to the buttons shows where the recording is now.",
            "Double-click a subtitle on the waveform to play from its start; click an empty spot to move the play position there.",
            "− and + zoom the waveform. “Controls” lists every key.",
          ],
        },
        {
          id: "translate",
          title: "Translate",
          steps: [
            "If you do not see the translation column, tick “Show translation” at the top of the editor (the choice is remembered).",
            "In the editor, the list's column that reads “Translation” is for the translation. Click it, or type into the “Translation” box under the English text.",
            "Above the box set the language code of the translation (cs, sk, de …); it names the translated files.",
            "Above the box you see the longest line and the reading speed of the translation against the type’s limits.",
            "To save the translation, tick “Translation” in the “Save” row of the save choices. The files are called like “lecture.cz-template.cs.srt”; subtitles not translated yet are left out of them. The Excel table always carries the translation in its Translation column.",
          ],
        },
        {
          id: "terms",
          title: "Terms and names",
          steps: [
            "In Settings, “Terms and names” holds your list of names and terms that the speech model writes wrong.",
            "Add them one per line: the right spelling, then = and what the speech model writes instead, separated by commas (for example: Nietzsche = Neechee).",
            "Press “Save settings”. Subtitles you have not edited follow the new list.",
          ],
        },
        {
          id: "types",
          title: "Your own subtitle types",
          steps: [
            "In Settings, “Subtitle types” lists the types the app has and your own.",
            "Make your own from a published guideline (“New type from a standard”) or as a copy of one the app has.",
            "Press “Change” to set its values: line length, lines, reading speed, timing, words, how strict each rule is.",
            "Press “Save settings”. Tick the type under “Ticked by default when saving” to make its subtitles with every new lecture.",
            "“Remove” puts a type aside: subtitles made with it still open and save, and “Put back” brings it back; “Delete for good” deletes a removed type.",
          ],
        },
        {
          id: "settings",
          title: "Settings and look",
          steps: [
            "In Settings choose which kinds of subtitles and file types are ticked by default, and where subtitles go.",
            "“This PC” shows whether the app uses the graphics card or the processor; “Measure again” re-measures the time estimates.",
            "At the top right switch between the light and dark look, and between Czech and English.",
          ],
        },
        {
          id: "remove",
          title: "Remove a lecture",
          steps: [
            "On a finished lecture press “Remove”. The app says what will be deleted and what stays, and asks first.",
            "It deletes the transcript, your edits and its own copies of the sound; your original file and saved subtitles stay.",
          ],
        },
        {
          id: "privacy",
          title: "Privacy and security",
          steps: [
            "Everything is processed on this computer; nothing is uploaded.",
            "What the app keeps is encrypted with a key locked to your Windows account. If the app cannot open its saved work (another Windows account or PC, or a missing key), “Start fresh” starts empty and keeps the old work aside, deleting nothing.",
            "Details are in “Security and privacy” on the app's Help page, and in the README.",
          ],
        },
      ],
    },
    title: "Help",
    how: {
      title: "How it works",
      steps: [
        "Add a lecture on the Queue page: choose files, drop them on the window, or paste a link to a recording. " +
          "Audio and video both work; of a video only the sound is used.",
        "Wait. Lectures are transcribed one after another; each shows its progress and how long it will take. You can " +
          "cancel at any time, and a transcription interrupted by closing the app goes on where it stopped.",
        "Save the subtitles: choose which kinds of subtitles and which file types you want, then press " +
          "“Save subtitles”. Saving again with other choices needs no new transcription.",
        "If you like, open “Edit subtitles” to check and correct them while you watch and listen. Your changes are " +
          "saved by themselves and go into every file you save afterwards.",
      ],
    },
    rules: "The two kinds of subtitles",
    formats: "File types",
    terms: {
      title: "Terms and names",
      text:
        "The speech model sometimes writes a name or a term the wrong way (Neechee instead of Nietzsche). List the " +
        "ones of your field in Settings, one per line: the right spelling, then the wrong ones after an equals sign. " +
        "The app then puts them right in the subtitles.",
    },
    inside: {
      title: "What happens inside",
      steps: [
        "The sound is taken from the recording and turned into one steady format; of a video only the sound is used.",
        "Speech detection finds where someone speaks and skips silence, music and noise, so nothing is invented there.",
        "The speech model (NVIDIA Parakeet) writes every word with the moment it starts and ends.",
        "Terms and names the model tends to misspell are put right, from your own list.",
        "The words are cut into subtitles by the chosen rules: length, lines, reading speed, gaps and good line breaks.",
        "A rule checker marks anything that breaks a rule; the editor shows it and lets you fix it.",
      ],
    },
    privacy: {
      title: "Security and privacy",
      groups: [
        {
          title: "What is processed where",
          points: [
            "Everything is processed on this computer: the sound, the speech recognition, the subtitles and the editor.",
            "Nothing is uploaded. The app has no account, no statistics and no tracking.",
            "It uses the internet in these cases only: once on the first start, to download the speech model; when you " +
              "paste a link, to download that recording (of a video only the sound is kept; the picture is never " +
              "saved); and once, if you ask for it, to download the support for an NVIDIA graphics card.",
          ],
        },
        {
          title: "Encryption: what the app keeps is locked",
          points: [
            "Everything the app keeps for itself (transcripts, subtitle drafts, the sound from links, copies of dropped " +
              "files, the listening copies, the queue and settings) is encrypted with AES-256-GCM, the strength banks use.",
            "The key is locked to your Windows account (Windows DPAPI). Another account on this PC, a copied, synced or " +
              "backed-up folder, a stolen disk, or a program that just reads files cannot read your work.",
            "If your Windows sign-in is lost (a reset password, a new account, a reinstalled system), the work kept in " +
              "the app is lost too. Saved subtitle files are not affected.",
            "Encryption cannot protect against a harmful program running under your own Windows account; no app can.",
          ],
        },
        {
          title: "Who can reach the app",
          points: [
            "The app answers only on this computer, never on the network.",
            "It answers only programs of the Windows account that started it, not other accounts signed in on this PC, " +
              "and not other web pages open in a browser.",
            "Its folders are readable by your account only.",
          ],
        },
        {
          title: "What is never written down",
          points: [
            "The secret part of a pasted link is never shown, never logged and never put in a message.",
            "The app's log never names your files.",
            "The app's window keeps no copies of transcripts or recordings.",
          ],
        },
        {
          title: "What is deleted, and when",
          points: [
            "Remove (on a finished lecture) asks first and then deletes the transcript, your edits and the listening " +
              "copy for good, together with the sound downloaded from a link or the copy of a dropped file.",
            "Your own original file and every subtitle file you saved are never deleted by the app.",
            "Saved subtitle files are ordinary, unencrypted files in the folder you choose, so other programs can use them.",
          ],
        },
      ],
    },
    keys: "Controls in the subtitle editor",
    about: {
      title: "About",
      version: (v: string): string => `Version ${v}`,
      what: "Turns English lectures into timed English subtitles, ready for translation.",
      madeBy: "Made by",
      author: "Martin Svoboda",
      site: "https://svobodamartin.dev",
      siteName: "svobodamartin.dev",
      creditsTitle: "Made with",
      licence: "Licence",
      changes: "Changes",
      credits: [
        {
          what: "Speech recognition",
          name: "NVIDIA Parakeet TDT 0.6B v2",
          by: "NVIDIA",
          licence: "CC BY 4.0 · https://creativecommons.org/licenses/by/4.0/",
          source: "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2",
          changes:
            "Converted to the ONNX format by istupakov (https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx); " +
            "this app runs that conversion as it is.",
        },
        {
          what: "Speech detection",
          name: "Silero VAD",
          by: "Silero Team (copyright 2020–present)",
          licence: "MIT",
          source: "https://github.com/snakers4/silero-vad",
          changes: "The ONNX version by istupakov (https://huggingface.co/istupakov/silero-vad-onnx), unchanged.",
        },
        {
          what: "Waveform",
          name: "wavesurfer.js",
          by: "katspaugh and contributors (copyright 2012–2023)",
          licence: "BSD 3-Clause",
          source: "https://github.com/katspaugh/wavesurfer.js",
          changes: "Unchanged; its licence is in THIRD-PARTY-NOTICES.txt in the app's folder.",
        },
        {
          what: "Window",
          name: "React",
          by: "Meta Platforms, Inc. and affiliates",
          licence: "MIT",
          source: "https://github.com/facebook/react",
          changes: "Unchanged; its licence is in THIRD-PARTY-NOTICES.txt in the app's folder.",
        },
      ],
      download:
        "Both models are downloaded from Hugging Face on the first start, in exactly the version this app was " +
        "tested with, and checked file by file.",
    },
  },

  result: {
    texts: "Save",
    original: "Original (English)",
    translation: "Translation",
    skipped: (names: string): string => `No translation yet, so no translated file for: ${names}.`,
    presets: "Subtitles",
    formats: "File types",
    save: "Save subtitles",
    saving: "Saving…",
    openFolder: "Open folder",
    saved: (n: number, folder: string): string => `Saved ${n} file${n === 1 ? "" : "s"} to ${folder}`,
    exists: (n: number): string =>
      n === 1 ? "A file with this name already exists." : `${n} files with these names already exist.`,
    replace: "Replace them",
    keep: "Keep the old ones",
    pickOne: "Choose at least one kind of subtitles, one file type and what to save (original or translation).",
    change: "Change",
    closeChoices: "Hide choices",
  },
  format: {
    srt: "SRT",
    vtt: "WebVTT",
    ass: "ASS",
    txt: "Text with times",
    xlsx: "Excel table",
  } as Record<string, string>,
  preset: {
    name: {} as Record<string, string>, // the preset files' own English names are used
    retired: (name: string): string => `${name} (removed type: opens subtitles made before)`,
    purpose: {
      "cz-template": "English cues sized so that the longer Czech translation still fits.",
      "en-subtitles": "Finished English subtitles for viewers.",
    } as Record<string, string>,
    limits: (p: PresetInfo): string =>
      `Up to ${p.max_chars_per_line} characters × ${p.max_lines} line${p.max_lines === 1 ? "" : "s"}, ` +
      `${num(p.max_duration_ms / 1000)} s on screen, ${num(p.max_cps)} characters per second.`,
  },
  infos: {
    presets:
      "Czech translation template: English subtitles kept shorter and slower to read, so the Czech translation, " +
      "which runs longer, still fits the same timing. English subtitles: finished subtitles for English viewers " +
      "at a normal reading speed. You can save both at once.",
    formats:
      "SRT: the most common subtitle file; opens in Subtitle Edit and almost every player. " +
      "WebVTT: for web pages and online players. ASS: subtitles with styling (font, position). " +
      "Text with times: plain text to read or print. Excel table: one row per subtitle and a Translation " +
      "column, filled where you have translated.",
    engine:
      "Parakeet is the speech recogniser this app uses: in our tests on lecture recordings the most accurate, " +
      "very fast on an NVIDIA graphics card and usable on the processor.",
    output:
      "Next to the original file: the subtitles get the video's name and go into the same folder. Dropped files " +
      "have no known folder, so their subtitles always go to the folder below. Existing files are never " +
      "replaced without asking.",
  },

  settings: {
    title: "Settings",
    engine: "Speech recognition",
    engineNames: {
      parakeet: "Parakeet (fast and accurate)",
    } as Record<string, string>,
    engineNote: "Applies to lectures added from now on.",
    defaults: "Ticked by default when saving",
    output: "Where to save subtitles",
    besideSource: "Next to the original file",
    inFolder: "Always in this folder",
    folder: "Folder",
    folderNote: "Dropped files have no known place, so their subtitles always go to this folder.",
    choose: "Choose…",
    reset: "Back to the app's folder",
    save: "Save settings",
    saving: "Saving…",
    saved: "Settings saved.",
    unsaved: "Unsaved changes: they count only once saved",
    leaveAsk:
      "Settings have unsaved changes (for example a new subtitle type). They are lost if you leave now. Leave without saving?",
    privacy: "Your work in this app",
    encrypted: (account: string): string =>
      "Everything the app keeps for itself (transcripts, subtitle drafts, copies of dropped files, previews) is " +
      `encrypted with your Windows account${account ? ` (${account})` : ""}. Other accounts, copies of the folder ` +
      "and programs that just read files cannot read it. If this Windows sign-in is lost (for example when " +
      "Windows is installed again), that work is lost too. Subtitle files you saved are ordinary files and stay.",
  },
  machine: {
    title: "This PC",
    onCard: (name: string): string => `Runs on the graphics card (${name}).`,
    unnamedCard: "NVIDIA",
    onCpu: "Runs on the processor.",
    why: {
      card: "",
      no_driver: "No NVIDIA graphics card was found (there is no NVIDIA driver on this PC).",
      no_card: "No NVIDIA graphics card was found.",
      card_too_old: "The NVIDIA graphics card is too old for the speech model.",
      card_too_small: "The NVIDIA graphics card has too little memory for the speech model.",
      no_card_support: "The graphics card cannot be used right now.",
      card_pack_missing: "The support for the graphics card is not downloaded yet.",
      driver_too_old:
        "The NVIDIA graphics card's driver is too old: update it (nvidia.com or Windows Update) and start the app again.",
    } satisfies Record<MachineInfo["reason"], string>,
    cpuSlower: "That works, only more slowly than on a graphics card.",
    perHour: (t: string): string => `About ${t} per hour of recording.`,
    perHourSoon: "Less than a minute per hour of recording.",
    notMeasured: "How fast this PC is has not been measured yet.",
    measuring: "Measuring how fast this PC is (up to a minute)…",
    measureWaiting: "The speed is measured after the transcription in progress.",
    byTest: "Measured by a short speed test.",
    byJobs: "Measured from the transcriptions so far.",
    threads: (n: number): string => `Processor cores in use: ${n}.`,
    measure: "Measure again",
    measureNote: "Runs after the transcription in progress, if any; takes up to a minute.",
  },
  glossary: {
    title: "Terms and names",
    info:
      "The speech model does not know every name and term of your field, so it writes some wrong (Neechee for " +
      "Nietzsche). Your list puts them right in the subtitles; the recognised words themselves are kept as they were.",
    builtin: (n: number): string => `Built into the app (${n})`,
    own: "Your own terms",
    placeholder: "Nietzsche = Neechee, Nichee\nDostoevsky / Dostoyevsky = Dusty Effsky\nSchrödinger",
    hint:
      "One term per line: the right spelling, then = and what the speech model writes instead, separated by " +
      "commas. Other right spellings go after /. A term alone is right as written and gets its capital where the " +
      "model wrote it small; ~ at the start of a line leaves its case alone. Lines starting with # are notes. " +
      "Applies to new subtitles and to untouched ones when they are opened or saved; subtitles you edited stay.",
  },
  error: {
    network: "The app did not answer. Is it still running?",
    unknown: "Something went wrong.",
  },
};

export type Strings = typeof en;

const cs: Strings = {
  appName: "TalkSubs",
  nav: {
    queue: "Fronta",
    settings: "Nastavení",
    help: "Nápověda",
    pages: "Stránky",
  },
  lang: { label: "Jazyk" },
  look: { label: "Vzhled", light: "Světlý vzhled", dark: "Tmavý vzhled" },
  info: { more: "Více informací", close: "Zavřít" },
  conn: { lost: "Spojení s aplikací se přerušilo. Připojuji znovu…" },
  paused: (_reason, kind) =>
    kind === "gpu_memory"
      ? "Čeká se: grafické kartě došla paměť, nejspíš ji zabírá jiný program. Fronta to za pár minut zkusí znovu " +
        "a naváže tam, kde přestala."
      : kind === "disk_full"
        ? "Čeká se: disk je plný, hotový přepis nejde uložit. Uvolněte místo na disku, kde je aplikace; fronta to " +
          "za pár minut zkusí znovu a naváže tam, kde přestala."
        : "Čeká se: na tomto počítači právě běží jiný přepis. Fronta pokračuje, jakmile skončí.",
  models: {
    title: "První spuštění: stahuje se řečový model",
    note:
      "Aplikace si řečový model stáhne jen jednou. Potom už pracuje bez internetu. Přednášky, které teď přidáte, " +
      "počkají, až bude stažený.",
    amount: (done, total) => `${done} z ${total}`,
    failedTitle: "Řečový model zatím není stažený",
    retry: "Zkusit znovu",
    label: "Stahování řečového modelu",
    error: {
      no_connection: () =>
        "Počítač není připojený k internetu, nebo není dostupná stránka huggingface.co. Model se stahuje jen jednou, " +
        "při prvním spuštění. Připojte se k internetu a stiskněte Zkusit znovu.",
      dropped: () => "Spojení se přerušilo. Stiskněte Zkusit znovu: stahování naváže tam, kde skončilo.",
      secure: () =>
        "Zabezpečené spojení s huggingface.co nejde navázat (možná mu brání firewall nebo firemní proxy). Stiskněte " +
        "Zkusit znovu, nebo to zkuste na jiné síti.",
      site: () => "Stránka s modelem odpověděla chybou. Zkuste to znovu později.",
      disk_full: () => "Disk se zaplnil. Uvolněte místo a stiskněte Zkusit znovu: stahování naváže tam, kde skončilo.",
      no_space: (need, free) =>
        `Na disku není dost volného místa: model potřebuje ${need}, na disku s aplikací je volných ${free}. ` +
        "Uvolněte místo a stiskněte Zkusit znovu.",
      damaged: () => "Stažená část modelu je poškozená. Stiskněte Zkusit znovu.",
    },
  },
  card: {
    label: "Podpora grafické karty",
    offer: (name) => `Tento počítač má grafickou kartu NVIDIA (${name}), kterou aplikace umí využít.`,
    note: (size) =>
      `Stáhněte jednou její podporu (${size}, oficiální soubory NVIDIA) a převod poběží na kartě, zhruba dvakrát ` +
      "rychleji (na počítači s pomalejším procesorem víc). Do té doby aplikace pracuje dál na procesoru.",
    download: (size) => `Stáhnout podporu grafické karty (${size})`,
    tip: "Stáhne soubory NVIDIA, které aplikace potřebuje k využití grafické karty; jen jednou, pak zase funguje bez internetu.",
    downloading: "Stahuje se podpora grafické karty",
    goingNote:
      "Převody mezitím běží dál na procesoru. Zavřením aplikace se stahování přeruší; při dalším spuštění naváže.",
    unpacking: "rozbaluje se…",
    failedTitle: "Podpora grafické karty zatím není stažená",
    error: {
      no_connection: () =>
        "Počítač není připojený k internetu, nebo nejsou dostupné soubory NVIDIA (files.pythonhosted.org). " +
        "Připojte se k internetu a stiskněte Zkusit znovu.",
      dropped: () => "Spojení se přerušilo. Stiskněte Zkusit znovu: stahování naváže tam, kde skončilo.",
      secure: () =>
        "Zabezpečené spojení s files.pythonhosted.org nejde navázat (možná mu brání firewall nebo firemní proxy). " +
        "Stiskněte Zkusit znovu, nebo to zkuste na jiné síti.",
      site: () => "Stránka se soubory odpověděla chybou. Zkuste to znovu později.",
      disk_full: () => "Disk se zaplnil. Uvolněte místo a stiskněte Zkusit znovu: stahování naváže tam, kde skončilo.",
      no_space: (need, free) =>
        `Na disku není dost volného místa: podpora potřebuje ${need}, na disku s aplikací je volných ${free}. ` +
        "Uvolněte místo a stiskněte Zkusit znovu.",
      damaged: () => "Stažená část je poškozená. Stiskněte Zkusit znovu.",
    },
  },
  locked: {
    text:
      "Práci uloženou v aplikaci tady nejde otevřít: zamkl ji jiný účet Windows nebo jiný počítač, chybí její klíč, " +
      "nebo je poškozená. Titulky, které už máte uložené, to neovlivní.",
    startFresh: "Začít znovu",
    confirm:
      "Začít s prázdnou aplikací? Stará práce se nesmaže: jen se přesune do složky „old-…“ ve složce dat aplikace.",
  },

  drop: {
    title: "Přidat přednášky",
    hint: "Přetáhněte sem zvukové nebo video soubory, nebo",
    button: "Vybrat soubory…",
    active: "Pusťte soubory sem",
    copyNote:
      "Přetažené soubory si aplikace nejdřív zkopíruje (u videa jen zvuk); soubory vybrané tlačítkem použije tam, " +
      "kde jsou.",
    choosing: "Vyberte soubory v okně Windows…",
    info:
      "Tlačítko „Vybrat soubory…“ použije soubory tam, kde jsou: nic se nekopíruje a nezabírá místo navíc. " +
      "Přetažený soubor si aplikace nejdřív zkopíruje, protože okno nevidí, kde leží. " +
      "Kopie se smaže, když položku odeberete ze seznamu.",
  },
  link: {
    label: "Nebo vložte odkaz na nahrávku",
    placeholder: "https://…",
    add: "Přidat odkaz",
    adding: "Přidávám…",
    note: "Stáhne se a uloží jen zvuk, zamčený; obraz se nikdy neukládá.",
  },
  upload: {
    waiting: "Čeká na zkopírování",
    sending: (done, total) => `Kopíruji ${done} z ${total}`,
    checking: "Kontroluji soubor…",
    failed: "Nepřidáno",
    notMedia: (name) => `${name} není zvukový ani video soubor.`,
    unreadable: (name) => `${name} nejde přečíst jako zvuk ani video.`,
    noSpace: "Na disku není dost místa na kopii tohoto souboru.",
    noRoom: (size, free, spare) =>
      `Na disku není dost místa na kopii tohoto souboru: zabere ${size}, TalkSubs si nechává ${spare} volných ` +
      `pro vlastní práci a teď je volných jen ${free}. Uvolněte na tom disku místo a přidejte soubor znovu.`,
    cancel: "Zrušit",
    dismiss: "Zavřít",
    list: "Kopírování",
  },

  queue: {
    title: "Fronta",
    empty: "Zatím tu nic není. Přidejte přednášku nahoře; až bude hotová, uložíte si titulky.",
    clearHint: "Hotové položky tu zůstanou, dokud je neodeberete.",
  },
  state: {
    fetching: "Stahuje se",
    queued: "Čeká",
    running: "Přepisuje se",
    done: "Hotovo",
    failed: "Selhalo",
    cancelled: "Zrušeno",
  },
  phase: {
    fetch: "Stahuji zvuk",
    load: "Načítám model řeči",
    decode: "Čtu zvuk",
    speech: "Hledám řeč",
    recognise: "Rozpoznávám řeč",
    done: "Dokončuji",
  },
  progress: {
    position: (done, total) => `${done} z ${total}`,
    eta: (left) => `zbývá asi ${left}`,
    etaSoon: "zbývá méně než minuta",
    etaUnknown: "odhaduji zbývající čas…",
  },
  job: {
    cancel: "Zrušit",
    cancelling: "Ruším…",
    remove: "Odebrat",
    removeAsk: "Odebrat tuto přednášku ze seznamu?",
    removeDeleted: "Natrvalo se smaže:",
    removeKept: "Zůstane:",
    removeWhat: {
      file: {
        deleted: "přepis, vaše úpravy v editoru a kopie k poslechu.",
        kept: "váš původní soubor tam, kde je, a všechny titulky, které jste uložili.",
      },
      upload: {
        deleted: "kopie, kterou si aplikace udělala z přetaženého souboru, přepis, vaše úpravy a kopie k poslechu.",
        kept: "váš původní soubor tam, odkud jste ho přetáhli, a všechny titulky, které jste uložili.",
      },
      link: {
        deleted: "zvuk stažený z odkazu, přepis, vaše úpravy a kopie k poslechu.",
        kept: "všechny titulky, které jste uložili. Chcete-li na přednášce pracovat znovu, vložte odkaz znovu.",
      },
    },
    removeConfirm: "Odebrat a smazat",
    removeKeep: "Ponechat",
    attempts: (n) => `Spuštěno ${n}×`,
    estimate: (t) => `potrvá asi ${t}`,
    estimateSoon: "potrvá méně než minutu",
  },
  preview: {
    waiting: "Náhled čeká na přípravu",
    making: (pct) => `Připravuji náhled… ${pct} %`,
    failed: (why) => `Náhled se nepodařilo vytvořit: ${why}`,
    show: "Ukázat náhled",
    hide: "Skrýt náhled",
    videoLabel: "Náhled videa přednášky",
    audioLabel: "Náhled zvuku přednášky",
  },

  types: {
    title: "Druhy titulků",
    info:
      "Druh titulků je sada pravidel: jak dlouhý smí být řádek, jak rychle se smí číst, jak dlouho titulek zůstane " +
      "na obrazovce. Vedle druhů, které má aplikace, si můžete udělat vlastní, podle zveřejněných zásad nebo " +
      "jako kopii, a změnit jeho hodnoty.",
    builtin: "(součást aplikace)",
    orderInfo:
      "Pořadí zde platí všude: ve frontě, v editoru i ve výsledku. Řádek přetáhněte za úchyt nebo použijte šipky, pak uložte.",
    dragTip: "Přetažením posunete tento druh výš nebo níž",
    up: "Posunout výš",
    down: "Posunout níž",
    copy: "Udělat kopii",
    copyTip: "Udělá z tohoto druhu váš vlastní, jehož hodnoty pak můžete změnit.",
    copyName: (name) => `${name} (moje kopie)`,
    edit: "Upravit",
    editTip: "Ukáže a umožní změnit hodnoty tohoto druhu.",
    close: "Zavřít",
    remove: "Odebrat",
    removeTip: "Odebere tento druh. Titulky s ním už udělané zůstanou; druh jde vrátit.",
    removeAsk: (name) =>
      `Odebrat druh „${name}“? Titulky s ním už udělané zůstanou a půjdou dál otevřít i uložit; ` +
      "druh se odloží stranou a jde vrátit.",
    fromStandard: "Nový druh podle zásad:",
    standardTip: "Zveřejněné zásady pro titulky, ze kterých můžete vyjít.",
    create: "Vytvořit",
    createTip: "Udělá váš vlastní druh s hodnotami těchto zásad; pak je můžete libovolně změnit.",
    full: "Máte 20 vlastních druhů, víc jich být nemůže. Odeberte jeden, chcete-li udělat další.",
    retired: (n) => `Odebrané druhy (${n})`,
    retiredNote: "Titulky udělané s těmito druhy jdou dál otevřít i uložit. Vraťte druh, chcete-li ho znovu používat.",
    restore: "Vrátit",
    forget: "Smazat natrvalo",
    retiredFull: "Odebraných druhů je 50, víc jich být nemůže. Nejdřív jeden z nich smažte natrvalo.",
    forgetTip: "Natrvalo smaže tento odebraný druh.",
    forgetAsk: (name) =>
      `Smazat druh „${name}“ natrvalo? Titulky s ním už udělané zůstanou v přednáškách, ale tento druh je už ` +
      "nepůjde vybrat k otevření ani uložení.",
    dropAsk: (name) => `Odebrat druh „${name}“? Ještě nebyl uložený, takže prostě zmizí.`,
    restoreTip: "Zpřístupní tento druh znovu.",
    saveNote: (button) => `Změny druhů se uloží tlačítkem „${button}“ dole na stránce.`,
    basedOn: (name) => `Vychází z: ${name}.`,
    name: "Název",
    description: "Popis",
    groupText: "Text",
    chars: "Znaků na řádek",
    charsTip: "Nejdelší povolený řádek. Většina norem používá 42 (televize 37).",
    lines: "Řádků na titulek",
    linesTip: "Jeden nebo dva řádky.",
    cps: "Rychlost čtení (znaků za sekundu)",
    cpsTip:
      "Jak rychle se titulek smí číst. Méně je pohodlnější: 12–15 je příjemné, 17 je obvyklé maximum pro češtinu, 20 pro angličtinu.",
    spaces: "Mezery se do rychlosti čtení počítají",
    spacesTip: "Netflix i české zásady mezery počítají; nechte zapnuté, pokud vaše norma neříká jinak.",
    groupTime: "Časování",
    minDur: "Nejkratší doba na obrazovce (s)",
    minDurTip: "Titulek zůstane aspoň tak dlouho, aby si ho divák všiml.",
    maxDur: "Nejdelší doba na obrazovce (s)",
    maxDurTip: "Delší úsek řeči se rozdělí do více titulků.",
    gap: "Mezera mezi titulky (políčka)",
    gapTip: "Nejkratší pauza mezi dvěma titulky, aby oko poznalo nový. Obvykle 2 políčka.",
    closeGaps: "Zúžit mezery do (políček)",
    closeGapsTip:
      "Takto krátká nebo kratší mezera se zúží na nejmenší mezeru, aby titulky plynule navazovaly. 0 to vypne.",
    leadIn: "Ukázat před řečí (ms)",
    leadInTip: "Titulek se smí objevit o tolik dřív než první slovo. Obvykle 0.",
    hold: "Zůstat po řeči (ms)",
    holdTip: "Nejdéle, co titulek zůstane po posledním slově, aby se dal dočíst.",
    grid: "Snímková frekvence pro nahrávky jen se zvukem",
    gridTip: "Časy titulků se kladou na políčka. Video si nechává svou frekvenci; tohle platí jen pro zvuk.",
    fps: (g) =>
      ({
        "24000/1001": "23,976 (film)",
        "24/1": "24",
        "25/1": "25 (Evropa)",
        "30000/1001": "29,97 (USA)",
        "30/1": "30",
      })[g] ?? g,
    groupWords: "Slova",
    fillers: "Vynechat „uh“, „um“ a podobné zvuky",
    fillersTip: "Přepis si je ponechá; vynechají je jen titulky.",
    capitals: "Žádné velké písmeno uprostřed věty po pauze",
    capitalsTip: "Model řeči píše první slovo po pauze s velkým písmenem; tady se změní na malé, kde věta nezačíná.",
    groupSeverity: "Jak přísné je které pravidlo",
    severityInfo:
      "Nutno opravit: červeně, editor to počítá jako chybu. Stojí za pohled: jen upozornění, které můžete nechat být.",
    hard: "Nutno opravit",
    soft: "Stojí za pohled",
    rule: {
      chars_per_line: "Znaků na řádek",
      lines: "Počet řádků",
      cps: "Rychlost čtení",
      min_duration: "Nejkratší doba na obrazovce",
      max_duration: "Nejdelší doba na obrazovce",
      min_gap: "Mezera mezi titulky",
      break_quality: "Dobré zalomení řádků",
    },
  },
  tips: {
    choose:
      "Otevře výběr zvukových nebo video souborů v tomto počítači. Zůstanou, kde jsou; aplikace si nechá jen " +
      "vlastní zašifrovanou kopii zvuku k poslechu.",
    addLink: "Stáhne z odkazu jen zvuk a přidá ho do fronty. Zvuk se uloží zašifrovaný.",
    cancelUpload: "Zastaví kopírování souboru do aplikace.",
    preview: "Přehraje přednášku tady (jen zvuk).",
    cancelJob: "Zastaví tento převod. Hotových přednášek se to netýká.",
    remove: "Odebere ze seznamu. Nejdřív se zeptá a řekne, co se smaže.",
    removeConfirm: "Natrvalo smaže soubory, které si aplikace k přednášce uložila. Uložené titulky zůstanou.",
    removeKeep: "Zavře otázku a přednášku ponechá.",
    save:
      "Uloží zaškrtnuté druhy titulků v zaškrtnutých typech souborů podle Nastavení: vedle původního " +
      "souboru, nebo do jedné složky.",
    edit: "Otevře editor: titulky zkontrolujete a opravíte při poslechu.",
    openFolder: "Otevře ve Windows složku s uloženými titulky.",
    change: "Určí, které druhy titulků a které typy souborů se uloží.",
    replace: "Přesto uloží a nahradí existující soubory.",
    keepFiles: "Neuloží nic; existující soubory zůstanou, jak jsou.",
    measure: "Změří, jak rychle tento počítač převádí, pro odhad času.",
    retryModel: "Zkusí znovu stáhnout model řeči.",
    chooseFolder: "Otevře výběr složky, kam se ukládají titulky.",
    resetFolder: "Vrátí výchozí složku aplikace pro titulky.",
    saveSettings: "Uloží toto nastavení.",
    look: "Přepne mezi světlým a tmavým vzhledem.",
    lang: "Jazyk okna.",
    startFresh: "Začne s prázdným úložištěm; staré se odloží stranou, nic se nesmaže.",
    preset: "Který druh titulků upravujete.",
    reopen: "Načte titulky znovu tak, jak jsou teď uložené.",
    undo: "Vrátí poslední změnu (Ctrl+Z).",
    redo: "Znovu provede vrácenou změnu (Ctrl+Y).",
    startOver: "Udělá tyto titulky znovu z nahrávky podle dnešních pravidel. Tlačítko Vrátit přinese ty původní zpět.",
    controls: "Jak editor ovládat klávesnicí a myší.",
    split: "Rozdělí titulek na dva u textového kurzoru (Ctrl+Enter).",
    merge: "Spojí titulek s následujícím (Ctrl+J).",
    insert: "Přidá nový titulek na místo přehrávání (Ctrl+I).",
    remove1: "Smaže tento titulek (klávesa Delete).",
    play: "Přehraje nebo pozastaví (mezerník).",
    nextProblem: "Přejde na další titulek, který porušuje pravidlo.",
    time: "Napište čas (třeba 1:02,5 nebo 0:01:02.500) a stiskněte Enter, nebo posuňte o jedno políčko tlačítky − a +.",
    translation: "Překlad tohoto titulku; ukládá se sám, stejně jako anglický text.",
    translationLanguage: "Kód jazyka překladu (cs, sk, de …); podle něj se pojmenují přeložené soubory.",
    startOverTranslated: "Tyto titulky mají překlad, proto je nelze znovu vytvořit z nahrávky.",
    texts: "Uloží anglické titulky, překlad, nebo obojí. Nepřeložené titulky v překladu chybějí.",
    showTranslation: "Ukáže nebo skryje sloupec a pole pro překlad (pro překládání). Zapamatuje se na tomto počítači.",
    showTranslationLocked: "Tyto titulky mají překlad, proto je vidět vždy.",
  },
  editor: {
    open: "Upravit titulky",
    back: "Zpět na frontu",
    title: (name: string): string => `Titulky: ${name}`,
    loading: "Otevírám titulky…",
    gone: "Tato přednáška už ve frontě není.",
    preset: "Druh titulků",
    saved: "Všechny změny jsou uložené",
    pending: "Zatím neuloženo",
    saving: "Ukládám…",
    failed: (why: string): string => `Neuloženo: ${why} Zkouším znovu…`,
    staleShort: "Tyto titulky se mezitím změnily jinde. Otevřete je znovu.",
    reopen: "Otevřít znovu",
    tools: "Nástroje úprav",
    undo: "↶ Vrátit",
    redo: "↷ Opakovat",
    startOver: "Vytvořit znovu z nahrávky…",
    startOverAsk:
      "Vytvořit tyto titulky znovu z nahrávky podle současných pravidel? Vaše úpravy v nich se nahradí. Tlačítko Vrátit je přinese zpět.",
    presetChanged:
      "Pravidla těchto titulků se od jejich úprav změnila. Titulky si nechávají pravidla, se kterými vznikly; „Vytvořit znovu z nahrávky“ použije nová.",
    noPreview: "Průběh zvuku a přehrávač se objeví, až bude náhled hotový.",
    previewFailed: "K této přednášce není náhled, a tak chybí průběh zvuku i přehrávač. Text se upravovat dá.",
    waveform: "Průběh zvuku s titulky",
    dragStart: "Tažením posunete začátek",
    dragEnd: "Tažením posunete konec",
    zoomIn: "Přiblížit",
    zoomOut: "Oddálit",
    play: "Přehrát",
    translation: "Překlad",
    translationLanguage: "Jazyk překladu",
    translationPlaceholder: "Sem napište překlad tohoto titulku (každý řádek textu je jeden řádek titulku).",
    translationStats: (longest, maxChars, rate, maxCps) =>
      `nejdelší řádek ${longest} / ${maxChars} znaků · ${num(rate).replace(".", ",")} / ${num(maxCps).replace(".", ",")} znaků za sekundu`,
    translationColumn: "Překlad",
    showTranslation: "Zobrazit překlad",
    pause: "Pozastavit",
    now: "Aktuální místo v nahrávce",
    stop: "■ Zastavit",
    stopTitle: "Zastavit a vrátit se na začátek nahrávky",
    jumpBack: "« 10 s",
    jumpBackTitle: "O 10 sekund zpět (klávesa ←)",
    jumpForward: "10 s »",
    jumpForwardTitle: "O 10 sekund vpřed (klávesa →)",
    list: "Seznam titulků",
    count: (n: number): string => `${n} ${cz(n, "titulek", "titulky", "titulků")}`,
    none: "Vyberte titulek v seznamu nebo na průběhu zvuku.",
    text: "Text vybraného titulku",
    textHint: "Každý řádek textu je jeden řádek titulku.",
    textLabel: "Text (anglicky)",
    cueOf: (n, of) => `Titulek ${n} z ${of}`,
    zoom: "Přiblížení",
    volume: "Hlasitost",
    start: "Začátek",
    end: "Konec",
    length: "Délka",
    speed: "Rychlost čtení",
    cps: (n: number): string => `${dec(n)} znaků/s`,
    seconds: (ms: number): string => `${dec(ms / 1000)} s`,
    earlier: "O 1 políčko dřív",
    later: "O 1 políčko později",
    split: "Rozdělit u kurzoru",
    merge: "Spojit s dalším",
    insert: "Nový titulek",
    remove: "Smazat",
    problems: (hard: number, soft: number): string =>
      hard + soft === 0 ? "Žádné pravidlo není porušené." : `Nutno opravit: ${hard} · Stojí za pohled: ${soft}`,
    checking: "Kontroluji…",
    nextProblem: "Další problém",
    noProblem: "V tomto titulku není co opravovat.",
    hard: "Nutno opravit",
    soft: "Stojí za pohled",
    keys: "Ovládání",
    keyList: [
      ["Mezerník", "Přehrát nebo pozastavit (mimo textové pole)"],
      ["←  →  (mimo textové pole)", "O 10 sekund zpět / vpřed"],
      ["Esc / Enter", "Opustit textové pole / vstoupit do něj"],
      ["↑ ↓  (v textovém poli Alt+↑ ↓)", "Předchozí / další titulek"],
      ["Ctrl+Z, Ctrl+Y", "Zpět, znovu"],
      ["Ctrl+Enter", "Rozdělit u kurzoru"],
      ["Ctrl+J", "Spojit s dalším titulkem"],
      ["Ctrl+I", "Nový titulek v místě přehrávání"],
      ["Delete  (mimo textové pole)", "Smazat titulek"],
      ["[  ]  (v textovém poli Alt+[ ])", "Začátek o 1 políčko dřív / později"],
      ["Shift+[  ]", "Konec o 1 políčko dřív / později"],
      ["+  −", "Přiblížit / oddálit průběh zvuku"],
      ["Ctrl+S", "Uložit hned"],
      ["Klik na titulek", "Vybere ho (v seznamu nebo na průběhu zvuku)"],
      ["Dvojklik na titulek v průběhu zvuku", "Přehraje od jeho začátku"],
      ["Tažení okraje titulku", "Posune jeho začátek nebo konec"],
      ["Tažení titulku", "Posune celý titulek, délka zůstane"],
      ["Klik na prázdné místo průběhu zvuku", "Přesune tam místo přehrávání (mezi titulky)"],
    ],
    rule: {
      order: () => "Začíná dřív než titulek nad ním.",
      overlap: (v: number, l: number) => `Překrývá se s titulkem nad ním o ${dec((l - v) / 1000)} s.`,
      off_grid: () => "Některý čas neleží na políčku videa.",
      empty: () => "Prázdný řádek nebo žádný text.",
      past_media_end: () => "Končí až po konci nahrávky.",
      chars_per_line: (v: number, l: number) => `Řádek má ${v} znaků (nejvýš ${l}).`,
      lines: (v: number, l: number) => `${v} ${cz(v, "řádek", "řádky", "řádků")} (nejvýš ${l}).`,
      max_duration: (v: number, l: number) => `Zobrazen ${dec(v / 1000)} s (nejvýš ${dec(l / 1000)} s).`,
      min_duration: (v: number, l: number) => `Zobrazen jen ${dec(v / 1000)} s (aspoň ${dec(l / 1000)} s).`,
      cps: (v: number, l: number) => `Čte se rychlostí ${dec(v)} znaků/s (nejvýš ${dec(l)}).`,
      min_gap: (v: number, l: number) =>
        `Jen ${v} ${cz(v, "políčko", "políčka", "políček")} po titulku nad ním (aspoň ${l}).`,
      break_quality: () => "Láme se na nešikovném místě (uvnitř jména nebo za malým slovem).",
    },
  },

  help: {
    guide: {
      title: "Krok za krokem",
      intro: "Vyberte téma; každé otevře krátký návod.",
      topics: [
        {
          id: "start",
          title: "První spuštění",
          steps: [
            "Při prvním spuštění se jednou stáhne model řeči (2,5 GB); okno ukazuje průběh a zbývající čas. Když se stahování přeruší, stiskněte „Zkusit znovu“: pokračuje, kde skončilo.",
            "Pak už aplikace pracuje bez internetu.",
            "Na počítači s grafickou kartou NVIDIA okno nabídne jednou stáhnout její podporu (1,7 GB, oficiální soubory NVIDIA); převod pak běží na kartě, zhruba dvakrát rychleji (na počítači s pomalejším procesorem víc). Do té doby, a na každém jiném počítači, běží na procesoru.",
          ],
        },
        {
          id: "add",
          title: "Přidání přednášky",
          steps: [
            "Na stránce Fronta stiskněte „Vybrat soubory…“ a vyberte zvukové nebo video soubory. Zůstanou, kde jsou.",
            "Nebo soubory přetáhněte do okna: aplikace si nechá vlastní zašifrovanou kopii jejich zvuku.",
            "Nebo vložte odkaz na nahrávku a stiskněte „Přidat odkaz“: stáhne se a uloží jen zvuk, zašifrovaný.",
            "Přednášky se přepisují jedna po druhé. Každá ukazuje průběh a zbývající čas; „Zrušit“ ji zastaví.",
            "Přepis přerušený zavřením aplikace nebo pádem pokračuje při dalším spuštění tam, kde skončil.",
          ],
        },
        {
          id: "listen",
          title: "Poslech přednášky",
          steps: ["U přednášky ve frontě stiskněte „Ukázat náhled“ a poslechněte si ji přímo v okně."],
        },
        {
          id: "save",
          title: "Uložení titulků",
          steps: [
            "U hotové přednášky stiskněte „Změnit“ a vyberte druhy titulků, typy souborů a co uložit (originál v angličtině, překlad, nebo obojí).",
            "Stiskněte „Uložit titulky“. Soubory se uloží vedle původního souboru, nebo do jedné složky, podle Nastavení (přetažené soubory a odkazy vždy do složky).",
            "Když soubory se stejným jménem už existují, aplikace se zeptá, zda je „Přepsat“, nebo ponechat.",
            "„Otevřít složku“ ukáže uložené soubory ve Windows. Nové uložení s jinou volbou nepotřebuje nový přepis.",
          ],
        },
        {
          id: "edit",
          title: "Úpravy titulků",
          steps: [
            "U hotové přednášky stiskněte „Upravit titulky“; nahoře vyberte, který druh titulků upravujete.",
            "Vyberte titulek v seznamu nebo na průběhu zvuku a opravte nahoře jeho text (každý řádek textu je jeden řádek titulku).",
            "Začátek nebo konec posunete tažením okraje na průběhu zvuku, tlačítky − a + (o jedno políčko), nebo napíšete čas a stisknete Enter.",
            "„Rozdělit u kurzoru“, „Spojit s dalším“, „Nový titulek“ a „Smazat“ mění samotné titulky. Tažením celého titulku v průběhu zvuku ho posunete i s jeho délkou; ↑ a ↓ (v textovém poli Alt+↑ ↓) přejdou na předchozí nebo další titulek.",
            "Červené značky ukazují, co je nutno opravit, žluté, co stojí za pohled; „Další problém“ přejde na další označený titulek.",
            "Vše se ukládá samo; „Vrátit“ a „Opakovat“ (Ctrl+Z, Ctrl+Y) vracejí a znovu provádějí změny.",
            "„Vytvořit znovu z nahrávky“ udělá titulky znovu podle dnešních pravidel (ne když mají překlad).",
          ],
        },
        {
          id: "player",
          title: "Přehrávání a pohyb",
          steps: [
            "„Přehrát“ spustí a pozastaví přehrávání (mezerník); „Zastavit“ zastaví a vrátí na začátek.",
            "„« 10 s“ a „10 s »“ skočí o deset sekund zpět nebo vpřed (klávesy ← a →).",
            "Velký čas vedle tlačítek ukazuje, kde nahrávka právě je.",
            "Dvojklik na titulek v průběhu zvuku přehraje od jeho začátku; klik na prázdné místo tam přesune místo přehrávání.",
            "− a + přibližují průběh zvuku. „Ovládání“ ukáže všechny klávesy.",
          ],
        },
        {
          id: "translate",
          title: "Překlad",
          steps: [
            "Když sloupec pro překlad nevidíte, zaškrtněte nahoře v editoru „Zobrazit překlad“ (volba se zapamatuje).",
            "V editoru je sloupec seznamu s nápisem „Překlad“ pro překlad. Klikněte do něj, nebo pište do pole „Překlad“ pod anglickým textem.",
            "Nad polem nastavte kód jazyka překladu (cs, sk, de …); podle něj se pojmenují přeložené soubory.",
            "Nad polem je vidět nejdelší řádek a rychlost čtení překladu proti mezím druhu titulků.",
            "Pro uložení překladu zaškrtněte při ukládání v řádku „Uložit“ „Překlad“. Soubory se jmenují třeba „prednaska.cz-template.cs.srt“; zatím nepřeložené titulky v nich chybějí. Tabulka Excel má překlad vždy ve sloupci Translation.",
          ],
        },
        {
          id: "terms",
          title: "Výrazy a jména",
          steps: [
            "V Nastavení je v části „Výrazy a jména“ váš seznam jmen a výrazů, které model řeči píše špatně.",
            "Přidávejte je jeden na řádek: správný tvar, pak = a čárkami oddělené tvary, které píše model řeči (třeba: Nietzsche = Neechee).",
            "Stiskněte „Uložit nastavení“. Titulky, které jste neupravovali, se řídí novým seznamem.",
          ],
        },
        {
          id: "types",
          title: "Vlastní druhy titulků",
          steps: [
            "V Nastavení najdete v části „Druhy titulků“ druhy, které má aplikace, i vaše vlastní.",
            "Vlastní druh uděláte podle zveřejněných zásad („Nový druh podle zásad“) nebo jako kopii druhu, který má aplikace.",
            "Tlačítkem „Upravit“ nastavíte hodnoty: délku řádku, počet řádků, rychlost čtení, časování, slova a přísnost pravidel.",
            "Stiskněte „Uložit nastavení“. Zaškrtněte druh v „Předem zaškrtnuté při ukládání“, aby se jeho titulky dělaly u každé nové přednášky.",
            "„Odebrat“ druh odloží: titulky s ním udělané jdou dál otevřít i uložit a „Vrátit“ ho vrátí; „Smazat natrvalo“ odebraný druh smaže.",
          ],
        },
        {
          id: "settings",
          title: "Nastavení a vzhled",
          steps: [
            "V Nastavení vyberete, které druhy titulků a typy souborů jsou předem zaškrtnuté a kam se titulky ukládají.",
            "„Tento počítač“ ukazuje, zda aplikace používá grafickou kartu, nebo procesor; „Změřit znovu“ přeměří odhad času.",
            "Vpravo nahoře přepnete mezi světlým a tmavým vzhledem a mezi češtinou a angličtinou.",
          ],
        },
        {
          id: "remove",
          title: "Odebrání přednášky",
          steps: [
            "U hotové přednášky stiskněte „Odebrat“. Aplikace řekne, co se smaže a co zůstane, a nejdřív se zeptá.",
            "Smaže přepis, vaše úpravy a své kopie zvuku; váš původní soubor a uložené titulky zůstanou.",
          ],
        },
        {
          id: "privacy",
          title: "Soukromí a zabezpečení",
          steps: [
            "Všechno se zpracovává na tomto počítači; nic se nikam neposílá.",
            "Co si aplikace ukládá, je zašifrované klíčem zamčeným k vašemu účtu Windows. Když aplikace svou uloženou práci neotevře (jiný účet Windows nebo počítač, chybějící klíč), „Začít znovu“ začne s prázdnou aplikací a starou práci odloží stranou, nic nesmaže.",
            "Podrobnosti jsou v části „Zabezpečení a soukromí“ na stránce Nápověda v aplikaci a v README.",
          ],
        },
      ],
    },
    title: "Nápověda",
    how: {
      title: "Jak to funguje",
      steps: [
        "Na stránce Fronta přidejte přednášku: vyberte soubory, přetáhněte je do okna, nebo vložte odkaz na " +
          "nahrávku. Jde zvuk i video; z videa se použije jen zvuk.",
        "Počkejte. Přednášky se přepisují jedna po druhé; u každé vidíte postup a jak dlouho to bude trvat. Kdykoli " +
          "můžete přepis zrušit, a když aplikaci zavřete, přepis potom pokračuje tam, kde přestal.",
        "Uložte titulky: vyberte, jaké titulky a jaké typy souborů chcete, a stiskněte „Uložit titulky“. Uložit " +
          "znovu s jinou volbou jde bez nového přepisu.",
        "Když chcete, otevřete „Upravit titulky“ a zkontrolujte je a opravte při přehrávání. Změny se ukládají samy " +
          "a dostanou se do každého souboru, který potom uložíte.",
      ],
    },
    rules: "Dva druhy titulků",
    formats: "Typy souborů",
    terms: {
      title: "Výrazy a jména",
      text:
        "Řečový model občas napíše jméno nebo výraz špatně (Neechee místo Nietzsche). Ty z vašeho oboru zapište " +
        "v Nastavení, na každý řádek jeden: správný tvar, za rovnítkem ty špatné. Aplikace je pak v titulcích " +
        "opraví.",
    },
    inside: {
      title: "Co se děje uvnitř",
      steps: [
        "Z nahrávky se vezme zvuk a převede se do jednoho stálého formátu; z videa se používá jen zvuk.",
        "Rozpoznání řeči najde místa, kde někdo mluví, a přeskočí ticho, hudbu i šum, takže tam nic nevymyslí.",
        "Model řeči (NVIDIA Parakeet) zapíše každé slovo i s okamžikem, kdy začíná a končí.",
        "Výrazy a jména, která model píše špatně, se opraví podle vašeho vlastního seznamu.",
        "Slova se rozdělí do titulků podle zvolených pravidel: délka, řádky, rychlost čtení, mezery a dobré zalomení.",
        "Kontrola pravidel označí, co některé pravidlo porušuje; editor to ukáže a nechá vás to opravit.",
      ],
    },
    privacy: {
      title: "Zabezpečení a soukromí",
      groups: [
        {
          title: "Co se kde zpracovává",
          points: [
            "Všechno se zpracovává na tomto počítači: zvuk, rozpoznání řeči, titulky i editor.",
            "Nic se nikam neposílá. Aplikace nemá žádný účet, žádné statistiky ani sledování.",
            "Internet použije jen v těchto případech: jednou při prvním spuštění, aby stáhla model řeči; když vložíte " +
              "odkaz, aby stáhla tu nahrávku (z videa si nechá jen zvuk, obraz se nikdy neukládá); a jednou, když o to " +
              "požádáte, aby stáhla podporu grafické karty NVIDIA.",
          ],
        },
        {
          title: "Šifrování: co si aplikace ukládá, je zamčené",
          points: [
            "Vše, co si aplikace ukládá pro sebe (přepisy, rozpracované titulky, zvuk z odkazů, kopie přetažených " +
              "souborů, kopie k poslechu, fronta i nastavení), je zašifrované šifrou AES-256-GCM, jakou používají banky.",
            "Klíč je zamčený k vašemu účtu Windows (Windows DPAPI). Jiný účet na tomto PC, zkopírovaná, synchronizovaná " +
              "či zálohovaná složka, ukradený disk ani program, který jen čte soubory, vaši práci nepřečte.",
            "Když o přihlášení do Windows přijdete (obnovené heslo, nový účet, přeinstalovaný systém), přijdete i o " +
              "práci uloženou v aplikaci. Uložených souborů s titulky se to netýká.",
            "Proti škodlivému programu, který běží pod vaším vlastním účtem Windows, šifrování chránit nemůže; to " +
              "nedokáže žádná aplikace.",
          ],
        },
        {
          title: "Kdo se k aplikaci dostane",
          points: [
            "Aplikace odpovídá jen na tomto počítači, nikdy po síti.",
            "Odpovídá jen programům účtu Windows, který ji spustil, ne jiným účtům přihlášeným na tomto PC a ne jiným " +
              "webovým stránkám otevřeným v prohlížeči.",
            "Její složky může číst jen váš účet.",
          ],
        },
        {
          title: "Co se nikdy nezapisuje",
          points: [
            "Tajná část vloženého odkazu se nikdy neukazuje, nezapisuje do záznamu ani neposílá ve zprávě.",
            "Záznam o činnosti aplikace nikdy neobsahuje jména vašich souborů.",
            "Okno aplikace si neschovává kopie přepisů ani nahrávek.",
          ],
        },
        {
          title: "Co se maže a kdy",
          points: [
            "Odebrat (u hotové přednášky) se nejdřív zeptá a pak natrvalo smaže přepis, vaše úpravy a kopii k poslechu, " +
              "spolu se zvukem staženým z odkazu nebo s kopií přetaženého souboru.",
            "Váš vlastní původní soubor ani uložené soubory s titulky aplikace nikdy nesmaže.",
            "Uložené titulky jsou obyčejné, nešifrované soubory ve složce, kterou si zvolíte, aby s nimi mohly " +
              "pracovat i jiné programy.",
          ],
        },
      ],
    },
    keys: "Ovládání editoru titulků",
    about: {
      title: "O aplikaci",
      version: (v) => `Verze ${v}`,
      what: "Z anglických přednášek dělá načasované anglické titulky připravené k překladu.",
      madeBy: "Autor",
      author: "Martin Svoboda",
      site: "https://svobodamartin.dev",
      siteName: "svobodamartin.dev",
      creditsTitle: "Postaveno na",
      licence: "Licence",
      changes: "Úpravy",
      credits: [
        {
          what: "Rozpoznávání řeči",
          name: "NVIDIA Parakeet TDT 0.6B v2",
          by: "NVIDIA",
          licence: "CC BY 4.0 · https://creativecommons.org/licenses/by/4.0/",
          source: "https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2",
          changes:
            "Převedeno do formátu ONNX (istupakov, https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx); " +
            "aplikace používá tento převod beze změn.",
        },
        {
          what: "Rozpoznání, kde se mluví",
          name: "Silero VAD",
          by: "Silero Team (copyright 2020–současnost)",
          licence: "MIT",
          source: "https://github.com/snakers4/silero-vad",
          changes: "Verze ONNX od istupakov (https://huggingface.co/istupakov/silero-vad-onnx), beze změn.",
        },
        {
          what: "Zvuková křivka",
          name: "wavesurfer.js",
          by: "katspaugh a další autoři (copyright 2012–2023)",
          licence: "BSD 3-Clause",
          source: "https://github.com/katspaugh/wavesurfer.js",
          changes: "Beze změn; jeho licence je v souboru THIRD-PARTY-NOTICES.txt ve složce aplikace.",
        },
        {
          what: "Okno",
          name: "React",
          by: "Meta Platforms, Inc. and affiliates",
          licence: "MIT",
          source: "https://github.com/facebook/react",
          changes: "Beze změn; jeho licence je v souboru THIRD-PARTY-NOTICES.txt ve složce aplikace.",
        },
      ],
      download:
        "Oba modely se stáhnou z Hugging Face při prvním spuštění, přesně ve verzi, se kterou byla aplikace " +
        "vyzkoušena, a každý soubor se zkontroluje.",
    },
  },

  result: {
    texts: "Uložit",
    original: "Originál (anglicky)",
    translation: "Překlad",
    skipped: (names) => `Tyto titulky zatím nemají překlad, přeložený soubor nevznikl: ${names}.`,
    presets: "Titulky",
    formats: "Typy souborů",
    save: "Uložit titulky",
    saving: "Ukládám…",
    openFolder: "Otevřít složku",
    saved: (n, folder) => `Uloženo: ${n} ${cz(n, "soubor", "soubory", "souborů")} do ${folder}`,
    exists: (n) =>
      n === 1
        ? "Soubor s tímto názvem už existuje."
        : `${n} ${cz(n, "soubor", "soubory", "souborů")} s těmito názvy už ${n <= 4 ? "existují" : "existuje"}.`,
    replace: "Přepsat",
    keep: "Ponechat staré",
    pickOne: "Vyberte aspoň jeden druh titulků, jeden typ souboru a co uložit (originál nebo překlad).",
    change: "Změnit",
    closeChoices: "Skrýt volby",
  },
  format: {
    srt: "SRT",
    vtt: "WebVTT",
    ass: "ASS",
    txt: "Text s časy",
    xlsx: "Tabulka Excel",
  },
  preset: {
    name: {
      "cz-template": "Šablona pro český překlad",
      "en-subtitles": "Anglické titulky",
    },
    retired: (name) => `${name} (odebraný druh: otevře titulky udělané dřív)`,
    purpose: {
      "cz-template": "Anglické titulky nakrájené tak, aby se do nich vešel delší český překlad.",
      "en-subtitles": "Hotové anglické titulky pro diváky.",
    },
    limits: (p) =>
      `Nejvýš ${p.max_chars_per_line} znaků × ${p.max_lines} ${cz(p.max_lines, "řádek", "řádky", "řádků")}, ` +
      `${num(p.max_duration_ms / 1000).replace(".", ",")} s na obrazovce, ` +
      `${num(p.max_cps).replace(".", ",")} znaků za sekundu.`,
  },
  infos: {
    presets:
      "Šablona pro český překlad: anglické titulky kratší a pomalejší na čtení, aby se do stejného časování " +
      "vešel delší český překlad. Anglické titulky: hotové titulky pro anglické diváky v běžné rychlosti čtení. " +
      "Uložit můžete obojí najednou.",
    formats:
      "SRT: nejběžnější soubor s titulky; otevře ho Subtitle Edit i skoro každý přehrávač. " +
      "WebVTT: pro webové stránky a přehrávače na internetu. ASS: titulky se stylem (písmo, umístění). " +
      "Text s časy: obyčejný text ke čtení nebo tisku. Tabulka Excel: jeden řádek na titulek a sloupec " +
      "Translation, vyplněný tam, kde jste přeložili.",
    engine:
      "Parakeet je rozpoznávač řeči, který aplikace používá: v našich zkouškách na nahrávkách přednášek byl " +
      "nejpřesnější, na grafické kartě NVIDIA je velmi rychlý a na procesoru použitelný.",
    output:
      "Vedle původního souboru: titulky dostanou jméno videa a uloží se do stejné složky. Přetažené soubory " +
      "známou složku nemají, proto jejich titulky jdou vždy do složky níže. Existující soubory se nikdy " +
      "nepřepíšou bez zeptání.",
  },

  settings: {
    title: "Nastavení",
    engine: "Rozpoznávání řeči",
    engineNames: {
      parakeet: "Parakeet (rychlý a přesný)",
    },
    engineNote: "Platí pro přednášky přidané od teď.",
    defaults: "Předem zaškrtnuté při ukládání",
    output: "Kam ukládat titulky",
    besideSource: "Vedle původního souboru",
    inFolder: "Vždy do této složky",
    folder: "Složka",
    folderNote: "Přetažené soubory nemají známé místo, proto jejich titulky jdou vždy do této složky.",
    choose: "Vybrat…",
    reset: "Zpět na složku aplikace",
    save: "Uložit nastavení",
    saving: "Ukládám…",
    saved: "Nastavení uloženo.",
    unsaved: "Neuložené změny: platí až po uložení",
    leaveAsk:
      "V Nastavení máte neuložené změny (třeba nový druh titulků). Když teď odejdete, ztratí se. Odejít bez uložení?",
    privacy: "Vaše práce v aplikaci",
    encrypted: (account: string): string =>
      "Vše, co si aplikace ukládá pro sebe (přepisy, rozpracované titulky, kopie přetažených souborů, náhledy), je " +
      `zašifrované vaším účtem Windows${account ? ` (${account})` : ""}. Jiné účty, kopie složky ani programy, ` +
      "které jen čtou soubory, to nepřečtou. Když se přihlášení do Windows ztratí (třeba po novém instalování " +
      "Windows), je ta práce pryč. Titulky, které už máte uložené, jsou obyčejné soubory a zůstanou.",
  },
  machine: {
    title: "Tento počítač",
    onCard: (name) => `Běží na grafické kartě (${name}).`,
    unnamedCard: "NVIDIA",
    onCpu: "Běží na procesoru.",
    why: {
      card: "",
      no_driver: "Grafická karta NVIDIA nebyla nalezena (v počítači není ovladač NVIDIA).",
      no_card: "Grafická karta NVIDIA nebyla nalezena.",
      card_too_old: "Grafická karta NVIDIA je pro rozpoznávání řeči příliš stará.",
      card_too_small: "Grafická karta NVIDIA má pro rozpoznávání řeči málo paměti.",
      no_card_support: "Grafickou kartu teď nejde použít.",
      card_pack_missing: "Podpora grafické karty zatím není stažená.",
      driver_too_old:
        "Ovladač grafické karty NVIDIA je příliš starý: aktualizujte ho (nvidia.com nebo Windows Update) a spusťte aplikaci znovu.",
    },
    cpuSlower: "Funguje to, jen pomaleji než na grafické kartě.",
    perHour: (t) => `Asi ${t} na hodinu záznamu.`,
    perHourSoon: "Méně než minuta na hodinu záznamu.",
    notMeasured: "Rychlost tohoto počítače ještě není změřená.",
    measuring: "Měřím rychlost tohoto počítače (nejvýš minutu)…",
    measureWaiting: "Rychlost se změří po právě běžícím přepisu.",
    byTest: "Změřeno krátkou zkouškou rychlosti.",
    byJobs: "Změřeno podle dosavadních přepisů.",
    threads: (n) => `Použitá jádra procesoru: ${n}.`,
    measure: "Změřit znovu",
    measureNote: "Spustí se po případném právě běžícím přepisu a trvá nejvýš minutu.",
  },
  glossary: {
    title: "Výrazy a jména",
    info:
      "Rozpoznávání řeči nezná všechny výrazy a jména vašeho oboru, takže některé píše špatně (Neechee místo " +
      "Nietzsche). Váš seznam je v titulcích opraví; samotná rozpoznaná slova zůstávají, jak byla.",
    builtin: (n) => `Vestavěné v aplikaci (${n})`,
    own: "Vaše vlastní výrazy",
    placeholder: "Nietzsche = Neechee, Nichee\nDostoevsky / Dostoyevsky = Dusty Effsky\nSchrödinger",
    hint:
      "Jeden výraz na řádek: správný tvar, pak = a to, co místo něj píše rozpoznávání řeči, oddělené čárkami. " +
      "Další správné tvary patří za /. Samotný výraz je správně tak, jak je napsaný, a dostane velké písmeno tam, " +
      "kde ho rozpoznávání napsalo malým; ~ na začátku řádku velikost písmen nechá být. Řádky začínající # jsou " +
      "poznámky. Platí pro nové titulky a pro titulky, na které jste nesáhli, když je otevřete nebo uložíte; " +
      "upravené zůstanou.",
  },
  error: {
    network: "Aplikace neodpovídá. Běží ještě?",
    unknown: "Něco se pokazilo.",
  },
};

const TABLES: Record<Lang, Strings> = { en, cs };
let current: Lang = "en";

/** The table for the window's language. */
export function s(): Strings {
  return TABLES[current];
}

export function setLanguage(lang: Lang): void {
  current = lang;
  if (typeof document !== "undefined") document.documentElement.lang = lang;
}

/** A preset's name and description in the current language (the preset file's own English as a fallback). */
export function presetText(p: PresetInfo): {
  name: string;
  description: string;
} {
  const S = s();
  const purpose = S.preset.purpose[p.id];
  const name = S.preset.name[p.id] ?? p.name;
  return {
    name: p.kind === "retired" ? S.preset.retired(name) : name,
    description: purpose ? `${purpose} ${S.preset.limits(p)}` : p.description,
  };
}

/** A published standard's name and description in the window's language (the files hold the English ones). */
export function standardText(p: Preset): { name: string; description: string } {
  const own = s() === cs ? STANDARD_CS[p.id] : undefined;
  return { name: own?.[0] ?? p.name, description: own?.[1] ?? p.description };
}

/** The published standards' names and descriptions in Czech (the files hold the English ones). */
const STANDARD_CS: Record<string, [string, string]> = {
  "lecture-style-czech": [
    "České titulky k přednáškám (podle titulkovani.cz)",
    "Anglické titulky pro český překlad přednášek, pohodlné ke čtení: 40 znaků × 2 řádky, 15 znaků za sekundu, 1,5 s až 6 s, bez vycpávkových slov, delší věty rozdělené v přirozených místech.",
  ],
  "lecture-style-czech-fast": [
    "České titulky k přednáškám, rychlá řeč (podle titulkovani.cz)",
    "Anglické titulky pro český překlad rychlých přednášek, na hranici čtení: 40 znaků × 2 řádky, 17 znaků za sekundu, 1,33 s až 6 s, bez vycpávkových slov, aby šel text zhustit.",
  ],
  "netflix-style-english": [
    "Podle Netflixu – anglické titulky",
    "Hotové anglické titulky podle průvodce Netflixu: 42 znaků × 2 řádky, 20 znaků za sekundu, 0,83 s až 7 s, mezery 2 políčka.",
  ],
  "netflix-style-czech": [
    "Podle Netflixu – rychlost pro češtinu",
    "Anglické titulky pro český překlad podle českého průvodce Netflixu: 42 znaků × 2 řádky, 17 znaků za sekundu, 0,83 s až 7 s.",
  ],
  "jtp-style-czech": [
    "Zásady českých překladatelů (JTP 2026)",
    "Anglické titulky pro český překlad podle Zásad tvorby titulků JTP: 42 znaků × 2 řádky, nejvýš 17 znaků za sekundu (nejlépe 12–15), 1 s až 7 s, bez vycpávkových slov.",
  ],
  "ted-style-talks": [
    "Podle TED – přednášky",
    "Titulky k přednáškám podle projektu TED: 42 znaků × 2 řádky, 21 znaků za sekundu, 1 s až 7 s, čistý text bez vycpávek.",
  ],
  "amazon-style": [
    "Podle Amazonu",
    "Titulky podle pravidel Prime Video: 42 znaků × 2 řádky, 17 znaků za sekundu, 0,83 s až 7 s, bez vycpávkových slov.",
  ],
  "bbc-style": [
    "Podle BBC",
    "Blízko řeči podle pravidel BBC: 37 znaků × 2 řádky (vysílání), asi 16 znaků za sekundu, mezery pod 1 s se zavřou, vycpávky zůstávají.",
  ],
  "asnep-style-slow": [
    "Pomalé čtení (podle ASNEP)",
    "Anglické titulky pro pomalé čtení s hodnotami českých pravidel ASNEP pro neslyšící: 42 znaků × 2 řádky, 12 znaků za sekundu, 2 s až 7 s, vycpávky zůstávají.",
  ],
  "amara-style": [
    "Podle Amary",
    "Rychlé titulky podle pravidel Amary: 42 znaků × 2 řádky, 25 znaků za sekundu, 1 s až 7 s.",
  ],
};
