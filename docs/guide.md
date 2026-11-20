# TalkSubs: step by step

The same guide is on the app's Help page. [Česky](navod.md) · [README](../README.md)

**Install**

1. Unzip TalkSubs into a folder you can write to that OneDrive does not sync (for example on another drive), not Program Files.
2. Double-click TalkSubs.bat. If Windows warns about a file from the internet, choose “More info” and “Run anyway”.

- [First start](#start)
- [Add a lecture](#add)
- [Listen to a lecture](#listen)
- [Save subtitles](#save)
- [Edit subtitles](#edit)
- [Play and move around](#player)
- [Translate](#translate)
- [Terms and names](#terms)
- [Your own subtitle types](#types)
- [Settings and look](#settings)
- [Remove a lecture](#remove)
- [Privacy and security](#privacy)

<a id="start"></a>
## First start

1. On the first start the speech model (2.5 GB) downloads once; the window shows its progress and the time left. If the download stops, press “Try again”: it goes on where it stopped.
2. From then on the app works without the internet.
3. On a PC with an NVIDIA graphics card the window offers to download its support once (1.7 GB, NVIDIA's official files); transcription then runs on the card, about twice as fast (more on a PC with a slower processor). Until then, and on any other PC, it runs on the processor.

<a id="add"></a>
## Add a lecture

1. On the Queue page press “Choose files…” and pick audio or video files. They stay where they are.
2. Or drop files on the window: the app keeps its own encrypted copy of their sound.
3. Or paste a link to a recording and press “Add link”: only the sound is downloaded and kept, encrypted.
4. Lectures are transcribed one after another. Each shows its progress and the time left; “Cancel” stops it.
5. A transcription interrupted by closing the app or a crash goes on where it stopped at the next start.

<a id="listen"></a>
## Listen to a lecture

1. On a lecture in the queue press “Show preview” to listen to it in the window.

<a id="save"></a>
## Save subtitles

1. On a finished lecture press “Change” to choose the kinds of subtitles, the file types and what to save (the original in English, the translation, or both).
2. Press “Save subtitles”. The files go beside the original file, or into one folder, as Settings says (dropped files and links always go to the folder).
3. If files of that name exist, the app asks whether to replace them (“Replace them”) or keep them.
4. “Open folder” shows the saved files in Windows. Saving again with other choices needs no new transcription.

<a id="edit"></a>
## Edit subtitles

1. On a finished lecture press “Edit subtitles”; at the top choose which kind of subtitles you edit.
2. Choose a subtitle in the list or on the waveform, and correct its text above (one subtitle line per text line).
3. Move its start or end: drag its edge on the waveform, use − and + (one frame), or type the time and press Enter.
4. “Split at the cursor”, “Join with the next”, “New subtitle” and “Delete” change the subtitles themselves. Drag a whole subtitle on the waveform to move it, keeping its length; ↑ and ↓ (Alt+↑ ↓ in the text box) go to the previous or next subtitle.
5. Red marks show what must be fixed, yellow ones what is worth a look; “Next problem” goes to the next marked subtitle.
6. Everything saves by itself; “Undo” and “Redo” (Ctrl+Z, Ctrl+Y) go back and forward.
7. “Remake from the recording” makes the subtitles again with today’s rules (not while they have a translation).

<a id="player"></a>
## Play and move around

1. “Play” plays and pauses (Space); “Stop” stops and goes back to the start.
2. “« 10 s” and “10 s »” jump ten seconds back or forward (← and → keys).
3. The large time next to the buttons shows where the recording is now.
4. Double-click a subtitle on the waveform to play from its start; click an empty spot to move the play position there.
5. − and + zoom the waveform. “Controls” lists every key.

<a id="translate"></a>
## Translate

1. If you do not see the translation column, tick “Show translation” at the top of the editor (the choice is remembered).
2. In the editor, the list's column that reads “Translation” is for the translation. Click it, or type into the “Translation” box under the English text.
3. Above the box set the language code of the translation (cs, sk, de …); it names the translated files.
4. Above the box you see the longest line and the reading speed of the translation against the type’s limits.
5. To save the translation, tick “Translation” in the “Save” row of the save choices. The files are called like “lecture.cz-template.cs.srt”; subtitles not translated yet are left out of them. The Excel table always carries the translation in its Translation column.

<a id="terms"></a>
## Terms and names

1. In Settings, “Terms and names” holds your list of names and terms that the speech model writes wrong.
2. Add them one per line: the right spelling, then = and what the speech model writes instead, separated by commas (for example: Nietzsche = Neechee).
3. Press “Save settings”. Subtitles you have not edited follow the new list.

<a id="types"></a>
## Your own subtitle types

1. In Settings, “Subtitle types” lists the types the app has and your own.
2. Make your own from a published guideline (“New type from a standard”) or as a copy of one the app has.
3. Press “Change” to set its values: line length, lines, reading speed, timing, words, how strict each rule is.
4. Press “Save settings”. Tick the type under “Ticked by default when saving” to make its subtitles with every new lecture.
5. “Remove” puts a type aside: subtitles made with it still open and save, and “Put back” brings it back; “Delete for good” deletes a removed type.

<a id="settings"></a>
## Settings and look

1. In Settings choose which kinds of subtitles and file types are ticked by default, and where subtitles go.
2. “This PC” shows whether the app uses the graphics card or the processor; “Measure again” re-measures the time estimates.
3. At the top right switch between the light and dark look, and between Czech and English.

<a id="remove"></a>
## Remove a lecture

1. On a finished lecture press “Remove”. The app says what will be deleted and what stays, and asks first.
2. It deletes the transcript, your edits and its own copies of the sound; your original file and saved subtitles stay.

<a id="privacy"></a>
## Privacy and security

1. Everything is processed on this computer; nothing is uploaded.
2. What the app keeps is encrypted with a key locked to your Windows account. If the app cannot open its saved work (another Windows account or PC, or a missing key), “Start fresh” starts empty and keeps the old work aside, deleting nothing.
3. Details are in “Security and privacy” on the app's Help page, and in the README.
