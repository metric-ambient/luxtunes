"""The short guides in Help → LuxTunes Help: (title, what it's about, the guide).

Each guide is a few lines of simple HTML. Keep them short, friendly and true to
the menus as they are; update a guide when its feature changes.
"""

GUIDES = [
    ("Getting started", "Bring your music in", """
<p>LuxTunes keeps your music, movies, TV shows, podcasts and audiobooks in one library.</p>
<ul>
<li><b>Coming from an iTunes library?</b> Choose <b>File → Library → Import iTunes Library…</b>
and pick its <i>iTunes Music Library.xml</i> file. Your songs, ratings, play counts and
playlists (smart ones too) come along.</li>
<li><b>Adding files:</b> <b>File → Add File to Library…</b> (Ctrl+O) or
<b>Add Folder to Library…</b>. Adding happens in the background; the display at the top
shows progress, with ✕ to stop.</li>
<li>Your library lives in one file, so nothing you do in LuxTunes changes where your
music is unless you ask it to (see <i>Your LuxTunes Media folder</i>).</li>
</ul>"""),

    ("Playing music", "Play, shuffle, repeat and Up Next", """
<ul>
<li>Double-click a song, or select it and press <b>Enter</b>. <b>Space</b> plays and
pauses.</li>
<li><b>Ctrl+→ / Ctrl+←</b> go to the next or previous song. <b>Ctrl+L</b> shows the song
that's playing.</li>
<li><b>Shuffle</b> and <b>Repeat</b> are under the sidebar and in the Controls menu.
Repeat goes Off → All → One. LuxTunes remembers both. Shuffle mixes the whole list
afresh each time, plays songs you haven't heard in the last couple of days first, and
keeps songs by the same artist or from the same album apart.</li>
<li><b>Up Next:</b> right-click songs → <b>Add to Queue</b> to play them next, or
<b>Add to End of Queue</b> to play them after everything already queued. Or drag them onto
<i>Up Next</i>. Those play first, then LuxTunes carries on where you were.</li>
<li>The song playing has a grey bar behind it, so it's easy to spot.</li>
</ul>"""),

    ("Mini Player and Pop-Out Player", "Smaller ways to see what's playing", """
<ul>
<li><b>View → Mini Player</b> (Ctrl+Shift+M) tucks the main window away and shows a small
player with the controls. Long names scroll. Click ⤢, or close it, to get the full
window back.</li>
<li><b>View → Pop-Out Player</b> (or click the album art in the corner) shows the
artwork large, with controls when you move the mouse over it. Drag its corner to resize.
Click <b>Lyrics</b> in its controls to show the song's lyrics over the artwork. When the
lyrics are timed, the line being sung is highlighted and stays in the middle (turn
this off in <b>Edit → Preferences… → Playback</b>).</li>
<li>No lyrics? Right-click songs → <b>Get Lyrics Online…</b>. After you say yes, LuxTunes
asks LRCLIB, a free lyrics library, and adds lyrics to songs that have none (and timing,
where LRCLIB has it).</li>
<li>The Mini Player stays on top of your other windows; turn that off in
<b>Edit → Preferences… → Playback</b>.</li>
<li>To look at a selected song's artwork without changing what's playing, right-click the
album art in the corner → <b>View Full Size Album Art</b>. Resize its window as you like.</li>
</ul>"""),

    ("Sleep timer", "Stop the music after a while", """
<ul>
<li><b>Controls → Sleep Timer…</b> (Ctrl+Shift+S): choose 15, 30 or 60 minutes, or 2, 4 or
8 hours. The window shows the time the music will stop.</li>
<li>While it runs, a ☾ button beside the volume shows the time left. Click it (or choose
<b>Controls → Turn Off Sleep Timer</b>) to turn it off.</li>
<li>At the end the music fades out and pauses; press Play to carry on.</li>
</ul>"""),

    ("Crossfade, gapless and Sound Check", "Edit → Preferences… → Playback", """
<ul>
<li><b>Crossfade songs</b> (1 to 15 seconds): the next song fades in as the current one
fades out.</li>
<li><b>Gapless playback</b>: the next song starts the moment the current one ends, for
live albums and continuous mixes.</li>
<li><b>Sound Check</b> plays songs at about the same volume. The first time, LuxTunes
checks your songs in the background (shown in the display, with ✕ to pause it).</li>
<li>These don't apply to movies and TV shows, or while Repeat One is on.</li>
</ul>"""),

    ("Playlists", "Make and fill your own lists", """
<ul>
<li><b>File → New Playlist</b> (Ctrl+N), or <b>New Playlist from Selection</b>
(Ctrl+Shift+N) with songs selected.</li>
<li>Drag songs onto a playlist in the sidebar, or right-click → <b>Add to Playlist</b>,
or copy and paste them (see <i>Cut, copy, paste and undo</i>).</li>
<li>If some songs are already in the playlist, LuxTunes asks whether to add them again
or skip them.</li>
<li>Rename a playlist by double-clicking it. Delete removes the playlist only, never
the songs.</li>
</ul>"""),

    ("Smart playlists", "Lists that fill themselves", """
<p>A smart playlist follows rules, like "Rating is 5 stars" or "Date Added in the last
2 months", and keeps itself up to date.</p>
<ul>
<li><b>File → New Smart Playlist…</b> (Ctrl+Alt+N). To change one, select it and choose
<b>File → Edit Smart Playlist…</b>.</li>
<li>You can't drag or paste songs into a smart playlist: its rules decide what's in
it.</li>
</ul>"""),

    ("Cut, copy, paste and undo", "The Edit menu", """
<ul>
<li><b>Copy</b> (Ctrl+C) and <b>Paste</b> (Ctrl+V) songs into a playlist or Up Next.
<b>Cut</b> (Ctrl+X) from one playlist and paste into another moves them.</li>
<li>Pasting into <b>Podcasts</b> or <b>Audiobooks</b>: a <i>copy</i> makes a second file of
that kind (the original stays in Music); a <i>cut</i>, or dragging songs there, moves
the song itself.</li>
<li>To move songs back, drag them onto <b>Music</b> (or cut and paste them there). A song
that's already in Music isn't pasted into Music again.</li>
<li><b>Movies</b> and <b>TV Shows</b> take only video files, and <b>Podcasts</b> and
<b>Audiobooks</b> only non-video files. Moving between Movies and TV Shows is fine, and
Music also takes music videos.</li>
<li><b>Undo</b> (Ctrl+Z) and <b>Redo</b> (Ctrl+Shift+Z) work for playlist changes,
moves between sections, pasted copies and removing songs from the library. The menu
says what it would undo.</li>
<li>Files moved to the Trash can't be brought back with Undo (look in your Trash
instead).</li>
</ul>"""),

    ("Get Info", "Edit a song's details", """
<ul>
<li>Select songs and press <b>Ctrl+I</b> (or right-click → <b>Get Info</b>).</li>
<li><b>Info</b>: name, artist, album, genre and more; changes are written into the
file too. <b>Artwork</b>: paste or choose a picture.</li>
<li><b>Options</b>: Volume Adjustment (it snaps back to None near the middle),
Equalizer Preset, Media Kind, Start and Stop Time, Remember playback position and
Skip when shuffling.</li>
<li><b>Video</b> (movies and TV shows): Show, Season Number, Episode ID, Episode Number and
Description.</li>
<li>With several songs selected you get <i>Multiple Item Information</i>: only the
fields you change are applied (a whole season's episodes at once, for example).</li>
</ul>"""),

    ("Movies, TV Shows, Podcasts, Audiobooks", "The Library sections", """
<ul>
<li>Videos you add go to <b>Movies</b>. To make one a TV episode, use Get Info →
Options → Media Kind, or drag it onto <b>TV Shows</b>.</li>
<li>Choose which sections show in <b>Edit → Preferences… → General</b>.</li>
<li>Movies play inside the window; press <b>F</b> or the full-screen button for full screen, <b>Esc</b> to leave it.</li>
<li>Right-click a video → <b>Create iPod Version</b> to make a copy an iPod can play.</li>
</ul>"""),

    ("Your LuxTunes Media folder", "Where your files live", """
<ul>
<li>With <b>Keep LuxTunes Media folder organized</b> on (Preferences → Advanced), files
are named and filed as Artist / Album / <i>Track Name</i>, and follow your edits.</li>
<li>Everything you add is copied into the LuxTunes Media folder, so your whole library
lives in one place; your originals stay where they were. Turn on <b>Move original files
on import</b> (Preferences → Advanced) to move them there instead.</li>
<li><b>Moved the folder to another drive?</b> Change its location in Preferences →
Advanced and click OK: songs that are in the new place are pointed there.</li>
<li><b>File → Library → Organize Library…</b> tidies everything at once.</li>
<li>Once a week LuxTunes keeps a copy of your library (ratings, plays, playlists) in a
<i>Backups</i> folder next to it, the last four weeks. To go back to one, choose
<b>File → Library → Restore from Backup…</b>; your library as it was is kept there too,
so you can change your mind.</li>
</ul>"""),

    ("Importing CDs", "Rip a CD into your library", """
<ul>
<li>Insert a CD: it appears under <b>Devices</b>. What happens on insert is set in
Preferences → General (for example <i>Ask to Import CD</i>).</li>
<li>LuxTunes asks before looking up the CD's names online.</li>
<li>Choose the format (MP3, AAC, Apple Lossless, AIFF or WAV) with
<b>Import Settings…</b> in Preferences.</li>
</ul>"""),

    ("Converting files", "MP3 and iPod versions", """
<ul>
<li>Right-click songs → <b>Create MP3 Version</b> (320 kbps) or <b>Create AAC Version</b>
(256 kbps). Each format offers the other: an MP3 gets an AAC version, an AAC file an MP3
one, and FLAC, WAV, AIFF, Apple Lossless and others both. Videos → <b>Create iPod
Version</b>. The new file is made beside the original. (Needs FFmpeg:
<code>sudo apt install ffmpeg</code>.)</li>
<li><b>Delete original after conversion</b> (Preferences → Advanced, off unless you turn
it on): the original goes to the Trash and the new version takes its place, keeping
its plays, rating and playlists.</li>
</ul>"""),

    ("Equalizer", "Shape the sound", """
<ul>
<li><b>View → Show Equalizer</b> (Ctrl+T). Tick <b>On</b>, then pick a preset or move the
sliders (that makes it <i>Manual</i>). Double-click a slider to reset it.</li>
<li><b>Make Preset…</b> saves the sliders under a name; <b>Edit List…</b> renames or
deletes presets.</li>
<li>Give a song its own preset in Get Info → Options → Equalizer Preset. It's used
while the equalizer is on.</li>
<li>Your equalizer goes to your phone when you sync it.</li>
</ul>"""),

    ("Syncing an iPod", "Put your music on an iPod", """
<ul>
<li>Plug the iPod in: it appears under <b>Devices</b>. Choose what to sync on its tabs
(Music, Movies, Podcasts, Photos…), then click <b>Sync</b>. The Music tab offers the
entire library with all playlists, the entire library with chosen playlists, or chosen
playlists, artists, albums and genres.</li>
<li>Works with iPod classic, mini, shuffle and nano 1st–5th generation, and the
original iPods. iPod nano 6th/7th generation and iPod touch can't be synced.</li>
<li>Play counts and ratings from the iPod come back to your library.</li>
<li>Songs on the iPod that are missing their album artwork get it back at the next
sync.</li>
<li>Click the eject button next to the iPod before unplugging it.</li>
</ul>"""),

    ("Syncing an Android phone", "Put your music on your phone", """
<ul>
<li>Unlock the phone, plug it in and choose <b>File transfer</b> in its USB notification.
It appears under <b>Devices</b>.</li>
<li>Choose internal storage or an SD card, and what to sync, then click <b>Sync</b>. On the
Music tab you can sync your entire library with all playlists or only the ones you
tick, or just chosen playlists, artists, albums and genres.
Everything goes in one <i>LuxTunes</i> folder that any music player can use.</li>
<li><b>Delete media not in this selection</b> keeps the phone tidy. LuxTunes only ever
touches its own folder.</li>
<li>With the LuxTunes app on the phone, plays, ratings, playlists and the equalizer
come back at the next sync. Songs you add on the phone are copied into your
library, and details you edit there (names, albums, years…) are saved here too.</li>
</ul>"""),

    ("Deleting songs", "Remove from a playlist or the library", """
<ul>
<li>In a playlist, <b>Delete</b> takes songs out of that playlist only.</li>
<li>In a Library section, LuxTunes asks whether to <b>Move to Trash</b> or
<b>Keep Files</b>. Keeping files only removes them from LuxTunes, and Undo can bring
them back.</li>
<li>If a file can't go to the Trash, LuxTunes says why (for example a drive without a
Trash) and lets you delete it for good, after asking again.</li>
</ul>"""),

    ("Views and finding things", "List, Album List, Grid, Cover Flow", """
<ul>
<li>Switch views with the buttons at the top or <b>View</b> (Ctrl+Shift+3 to 6).
<b>View Options…</b> (Ctrl+J) and a right-click on the column headers choose columns.</li>
<li>Click a column header to sort by it. Each Library section and playlist keeps its own
sort, and where you'd scrolled to while LuxTunes is open.</li>
<li>Type in the search box to filter what's shown. Click the magnifier to search only
one kind of tag: Artist, Album, Composer or Song (All searches them all).</li>
<li>Each Library section (Music, Movies, TV Shows…) keeps its own columns and their
order; playlists use Music's.</li>
<li><b>File → Display Duplicates</b> and <b>Display Missing</b> help clean up.</li>
</ul>"""),

    ("Updates and privacy", "Nothing goes online without asking", """
<ul>
<li>LuxTunes only goes online when you agree: CD names, Get Track Info Online…, Get Lyrics
Online…, and updates.</li>
<li><b>Help → Check for Updates…</b> looks for a new version (after asking) and offers
to install it.</li>
</ul>"""),
]
