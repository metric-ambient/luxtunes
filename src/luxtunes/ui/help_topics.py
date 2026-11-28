"""The short guides in Help → LuxTunes Help: (title, what it's about, the guide).

Each guide is a few lines of simple HTML. Keep them short, friendly, and true to
the menus as they are; update a guide when its feature changes.
"""

GUIDES = [
    ("Getting started", "Bring your music in", """
<p>LuxTunes keeps your music, movies, TV shows, podcasts, and audiobooks in one library.</p>
<ul>
<li><b>Coming from an iTunes library?</b> Choose <b>File → Library → Import iTunes Library…</b>
and pick its <i>iTunes Music Library.xml</i> file. Your songs, ratings, play counts, and
playlists (smart ones too) come along.</li>
<li><b>Adding files:</b> <b>File → Add File to Library…</b> (Ctrl+O) or
<b>Add Folder to Library…</b>. Adding happens in the background; the display at the top
shows progress, with ✕ to stop.</li>
<li>What you add is copied into your LuxTunes Media folder, so your whole library lives in
one place; the originals stay where they were. Or turn off <b>Keep LuxTunes Media folder
organized</b> and songs stay where they are (see <i>Your LuxTunes Media folder</i>).
Your ratings, plays, and playlists are kept in LuxTunes' library file.</li>
</ul>"""),

    ("Playing music", "Play, shuffle, repeat, and Up Next", """
<ul>
<li>Double-click a song, or select it and press <b>Enter</b>. <b>Space</b> plays and
pauses.</li>
<li><b>Ctrl+→ / Ctrl+←</b> go to the next or previous song. <b>Ctrl+L</b> shows the song
that's playing.</li>
<li><b>Shuffle</b> and <b>Repeat</b> are either side of the playback buttons in the bottom
bar (lit, with a dot under them, while on), and in the Controls menu.
Repeat goes Off → All → One. LuxTunes remembers both. Shuffle mixes the whole list
afresh each time and leans towards albums you haven't played or skipped anything from in
the last two weeks; songs you heard in the last couple of days come last. In a big
library an album doesn't come back for hours, and an artist not for dozens of songs;
albums and artists with lots of songs come up as often as their size says, but spread
out. Change or clear the search while
the list plays and what plays next follows it.</li>
<li><b>Up Next:</b> right-click songs → <b>Add to Queue</b> to play them next, or
<b>Add to End of Queue</b> to play them after everything already queued. Or drag them onto
<i>Up Next</i>. Those play first, then LuxTunes carries on where you were.</li>
<li>The song playing has a grey bar behind it, so it's easy to spot.</li>
</ul>"""),

    ("Radio Mix", "Endless mixes of related songs", """
<ul>
<li>Right-click a song → <b>Start Radio Mix</b> → <b>From This Song</b>, <b>From This Album</b>
or <b>From This Artist</b>. A mix of related songs from your library starts playing, under
<i>PLAYING → Radio Mix</i> (“Ember Lane Radio”, say). Starting another one replaces it.</li>
<li>Songs are related by genre (LuxTunes knows how about 3,300 genres connect), helped by
your own playlists, plays, and ratings. Giving songs a genre helps a lot.</li>
<li>It starts with 50 songs and keeps adding more near the end, so it doesn't stop.</li>
<li>In front of each song: <b>♥</b> likes it on mixes like this one; <b>⊘</b> (not
interested) skips it, takes it off the mix, and keeps it off mixes like it; taking a song
off the mix any other way (Delete, Remove from Radio Mix) counts the same. Click ♥ or ⊘
again to undo.</li>
<li>In the bottom bar, beside the playback buttons: <b>Save as Playlist</b> keeps the mix as a playlist (shown with its
name ready to edit); <b>Refresh</b> makes a new mix; <b>Discover</b> (lit while it's on)
prioritizes related songs you rarely or never play; <b>Likes &amp; Dislikes…</b> (also
View → Radio Mix Likes &amp; Dislikes) lists every song you've liked or disliked, where
Delete sets one back to neutral.</li>
<li>Everything is worked out on your computer; nothing goes online.</li>
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
<li>No lyrics? Right-click songs → <b>Get Lyrics Online…</b>. After you say yes (or straight
away, if Preferences → Network allows it), LuxTunes asks LRCLIB, a free lyrics library, and adds lyrics to songs that have none (and timing,
where LRCLIB has it).</li>
<li>The Mini Player stays on top of your other windows; turn that off in
<b>Edit → Preferences… → Playback</b>.</li>
<li>To look at a selected song's artwork without changing what's playing, right-click the
album art in the corner → <b>View Full Size Album Art</b>. Resize its window as you like.</li>
</ul>"""),

    ("The window", "What's where", """
<ul>
<li><b>Along the top:</b> the library's sections as tabs (Music, Movies, TV Shows, Podcasts,
Audiobooks), the song playing in the middle, then the views (List, Album, Grid, Carousel),
and the search box.</li>
<li><b>Along the bottom:</b> the album art (click it for the Pop-Out Player), the sleep
timer's moon, shuffle, previous, play, next, and repeat with the song's position, then the
buttons for what's on screen (an iPod's <b>Sync</b>, a CD's <b>Import CD</b>, <b>Display
All</b>, and the Radio Mix's and podcasts' buttons), and the volume with your library's totals
under it. Short messages show there for a moment.</li>
<li>The <b>up arrow</b> beside the art opens it big, over the bottom-left corner; the
<b>left arrow</b> above it folds the sidebar away (and brings it back), as does
<b>View → Show Sidebar</b>. While the sidebar is folded, the art stays small.</li>
<li><b>+</b> and the bolt beside <b>PLAYLISTS</b> make a new playlist or smart playlist.</li>
</ul>"""),

    ("Appearance", "Styles and colors (View → Appearance…)", """
<ul>
<li><b>View → Appearance…</b> chooses how LuxTunes looks, the same way as the LuxTunes app
for Android: three styles, <b>Dark</b>, <b>Material</b> (the one LuxTunes starts with) and
<b>Light</b>.</li>
<li>Each style has its own <b>background</b>, <b>text</b> and <b>accent</b> color. Click a
color to pick another: a suggestion, any color (More Colors…), or a hex value. Everything
else (bars, the sidebar, row stripes, buttons) is mixed from those three, so text stays
readable. Right-click a style to put its colors back.</li>
<li><b>Auto Accent</b> takes the accent from the playing song's album art, when the art has a
color that stands out on the background; otherwise your own accent is used.</li>
<li>The accent marks what's selected and the song that's playing.</li>
<li>On Linux, LuxTunes draws its own title bar in the style's colors, with the menus in it.
Untick <i>LuxTunes' own title bar</i> in View → Appearance… for the desktop's own (from the
next time LuxTunes opens).</li>
</ul>
"""),
    ("Sleep timer", "Stop the music after a while", """
<ul>
<li>Click the moon in the bottom bar (beside the album art's arrow), or choose
<b>Controls → Sleep Timer…</b> (Ctrl+Shift+S): a small list opens above the moon with 15, 30,
or 60 minutes, or 2, 4, or 8 hours, each with the time the music would stop. Click one to
start; click anywhere else (or press Esc) to close the list.</li>
<li>While the timer runs the moon is lit and shows the time left under it; click it (or
choose <b>Controls → Turn Off Sleep Timer</b>) to turn it off.</li>
<li>At the end the music fades out and pauses; press Play to carry on.</li>
</ul>"""),

    ("Crossfade, gapless, and Sound Check", "Edit → Preferences… → Playback", """
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
(Ctrl+Shift+N) with songs selected, or <b>+</b> beside <b>PLAYLISTS</b> in the sidebar (the
bolt beside it makes a smart playlist).</li>
<li>Drag songs onto a playlist in the sidebar (when you drag several, a small icon shows
how many), or right-click → <b>Add to Playlist</b>, or copy and paste them (see
<i>Cut, copy, paste and undo</i>).</li>
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

    ("Cut, copy, paste, and undo", "The Edit menu", """
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
moves between sections, pasted copies, and removing songs from the library. The menu
says what it would undo.</li>
<li>Files moved to the Trash can't be brought back with Undo (look in your Trash
instead).</li>
</ul>"""),

    ("Get Info", "Edit a song's details", """
<ul>
<li>Select songs and press <b>Ctrl+I</b> (or right-click → <b>Get Info</b>).</li>
<li><b>Info</b>: name, artist, album, genre, and more; changes are written into the
file too. <b>Lyrics</b>: type or paste them. <b>Artwork</b>: paste or choose a
picture.</li>
<li>While you type an artist, album, composer, or grouping, names already in your library
are suggested. <b>Genre</b> suggests yours first, then LuxTunes' list of about 3,300
genres. A genre LuxTunes doesn't know is fine, but it's noted under the box, since it
can't be matched with related genres.</li>
<li><b>Genre</b>, <b>Featured Artist</b> and <b>Artists</b> can hold several names, each
shown as a pill: type a name and press <b>Enter</b> (or pick a suggestion) to add it,
click its ✕ to remove it, double-click it (or press Backspace) to change it, and use ▾
to choose from your library. Grid → Genres shows a song under each of its genres.</li>
<li><b>Artists</b> is for a song by several artists: the Artist stays as you want it
shown (<i>Lümen &amp; Grayfield</i>), and Artists lists each of them (<i>Lümen</i>,
<i>Grayfield</i>), so Grid → Artists and Radio Mix find the song under each. When both
already have songs of their own, Get Info suggests them. In the song list's Artists and
Featured Artist columns, names are separated by semicolons (<i>Lümen; Grayfield</i>),
since a comma can be part of a name.</li>
<li>In Grid → Artists, a compilation's songs stay together under <i>Various Artists</i>
(or its Album Artist), and also show under each of their artists who have songs outside
compilations. An artist who's only ever on compilations doesn't get an entry of their
own.</li>
<li><b>Options</b>: Volume Adjustment (it snaps back to None near the middle),
Equalizer Preset, Media Kind, Start and Stop Time, Remember playback position, and
Skip when shuffling.</li>
<li><b>Video</b> (movies and TV shows): Show, Season Number, Episode ID, Episode Number, and
Description.</li>
<li>With several songs selected you get <i>Multiple Item Information</i>: only the
fields you change are applied (a whole season's episodes at once, for example).</li>
<li>Right-click → <b>Get Track Info Online…</b> looks songs up on MusicBrainz (after you
say yes, or straight away if Preferences → Network allows it) and shows every suggested correction, and missing covers, for you to tick
before anything changes. A credit like <i>Lümen &amp; Grayfield feat. Ann</i> fills in the
Artist, each of the Artists, and the Featured Artist; genres come from the song, else its
album. If MusicBrainz is busy, LuxTunes waits a moment and tries again.
<b>Get Lyrics Online…</b> fills in missing lyrics.</li>
</ul>"""),

    ("Movies, TV Shows, Podcasts, Audiobooks", "The Library sections", """
<ul>
<li>The library's sections are the tabs at the top left: <b>Music</b>, <b>Movies</b>,
<b>TV Shows</b>, <b>Podcasts</b> and <b>Audiobooks</b>. Preferences → General → Sources
chooses which show.</li>
<li>Videos you add go to <b>Movies</b>. To make one a TV episode, use Get Info →
Options → Media Kind, or drag it onto the <b>TV Shows</b> tab.</li>
<li>Choose which sections show in <b>Edit → Preferences… → General</b>.</li>
<li>Movies play inside the window; press <b>F</b> or the full-screen button for full screen, <b>Esc</b> to leave it.</li>
<li>Right-click a video → <b>Create iPod Version</b> to make a copy an iPod can play.</li>
</ul>"""),

    ("Your LuxTunes Media folder", "Where your files live", """
<ul>
<li>With <b>Keep LuxTunes Media folder organized</b> on (Preferences → Advanced, the
usual way), everything you add is copied into the LuxTunes Media folder, so your whole
library lives in one place, and files are named and filed as Artist / Album /
<i>Track Name</i> and follow your edits. Your originals stay where they were; turn on
<b>Move original files on import</b> to move them there instead.</li>
<li>Turn it off to use a folder just as it is, for example a shared drive other programs
read too: choose it as the media folder, and songs you add from it (or anywhere) stay
where they are. LuxTunes never renames or moves them. New files it makes itself (CDs you
import, converted copies, and songs from an iPod or phone) still go into the media folder.</li>
<li><b>Moved the folder to another drive?</b> Change its location in Preferences →
Advanced and click OK: songs that are in the new place are pointed there.</li>
<li><b>File → Library → Organize Library…</b> tidies everything at once (only while the
folder is kept organized).</li>
<li>Once a week LuxTunes keeps a copy of your library (ratings, plays, playlists) in a
<i>Backups</i> folder next to it, the last four weeks. To go back to one, choose
<b>File → Library → Restore from Backup…</b> and pick a week. Before it restores, LuxTunes
saves your library as it is right now into the Backups folder, so if you change your mind
you can restore that copy the same way.</li>
</ul>"""),

    ("Importing CDs", "Rip a CD into your library", """
<ul>
<li>Insert a CD: it appears under <b>Devices</b>. What happens on insert is set in
Preferences → General (for example <i>Ask to Import CD</i>).</li>
<li>LuxTunes asks before looking up the CD's names online (unless Preferences → Network
allows it without asking).</li>
<li>Choose the format (MP3, AAC, Apple Lossless, AIFF, or WAV) with
<b>Import Settings…</b> in Preferences.</li>
</ul>"""),

    ("Converting files", "MP3, AAC, and iPod versions", """
<ul>
<li>Right-click songs → <b>Create MP3 Version</b> (320 kbps) or <b>Create AAC Version</b>
(256 kbps). Each format offers the other: an MP3 gets an AAC version, an AAC file an MP3
one, and FLAC, WAV, AIFF, Apple Lossless, and others both. From an MP3, AAC, Ogg, or Opus
file the new version is never made at a higher bit rate than the original (a 128 kbps
MP3 makes a 128 kbps AAC): re-encoding can't bring back quality. Videos → <b>Create iPod
Version</b>. The new file is made beside the original. (This uses FFmpeg, which comes
with the LuxTunes AppImage; a copy of LuxTunes installed another way needs it installed:
<code>sudo apt install ffmpeg</code>.)</li>
<li><b>Delete original after conversion</b> (Preferences → Advanced, off unless you turn
it on): the original goes to the Trash and the new version takes its place, keeping
its plays, rating, and playlists.</li>
<li><b>No need to convert for your iPod:</b> keep FLACs in your library. Songs the iPod
can't play (FLAC, Ogg, Opus) are converted while they sync, and only the iPod's copy
changes. On the iPod's Summary, <b>Sync lossless songs as</b> chooses AAC (small) or
Apple Lossless (full quality, about 4–5 times bigger). High-resolution FLACs become
16-bit, 44.1 or 48 kHz, which the iPod plays. Phones play FLAC as it is.</li>
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
(Music, Movies, Podcasts, Audiobooks, Photos…), then click <b>Sync</b>. The Music tab offers the
entire library with all playlists, the entire library with chosen playlists, or chosen
playlists, artists, albums, and genres.</li>
<li>Click anywhere on a row (or a movie's or audiobook's cover) to tick it. On the
TV Shows and Podcasts tabs, clicking a show lists its episodes, so tick its box to sync it.
Ticked shows are used with "episodes of selected shows"; ticked episodes always go.</li>
<li>The search box filters the lists on the tab you're looking at, so a long list of
artists or albums is quick to go through. Ticked rows stay ticked while hidden.</li>
<li>Works with iPod classic, mini, shuffle, and nano 1st–7th generation, and the
original iPods. iPod touch can't be synced.</li>
<li>Before syncing an iPod nano 6th or 7th generation, LuxTunes keeps a copy of its song
list (in <i>~/.local/share/luxtunes/iPod backups</i>), in case it needs to be put back.</li>
<li><b>New iPod sync engine (experimental):</b> Preferences → Advanced → <b>Use the new iPod
sync engine</b> has LuxTunes write the iPod's song list, album artwork, and signatures itself,
in place of libgpod (ported from ZenPod). It works with every iPod above except the
shuffles. With it on, a copy of the song list (and the artwork list) is kept before every
sync, and what was written is read back to check it. An iPod nano 5th generation must have
been synced with iTunes once, as with libgpod.</li>
<li>Play counts and ratings from the iPod come back to your library.</li>
<li><b>An iPod synced with another computer?</b> When it's plugged in, LuxTunes offers to
add the songs on it that aren't in your library: they're copied into your library with
their details, play counts, and ratings, and nothing on the iPod changes. Do it before
syncing, since a sync takes songs that aren't in your library off the iPod. To do it
later, right-click the iPod → <b>Add Songs to Library…</b>. Songs bought from the iTunes
Store with copy protection can't be played here, so they're left out.</li>
<li>Songs on the iPod that are missing their album artwork get it back at the next
sync.</li>
<li>While it syncs, the iPod's <b>Sync</b> button (in the bottom bar) becomes <b>Stop Sync</b>: it stops after
the song being copied, and what's already copied stays.</li>
<li>Click the eject button next to the iPod before unplugging it.</li>
<li><b>Rockbox or RepriseOS on the iPod too?</b> The iPod's Summary shows them under
<b>Also Runs</b>. One sync serves all of them. RepriseOS reads Apple's song list like the
iPod's own software. Rockbox reads the song files themselves, so with
<b>Make songs and playlists work in Rockbox too</b> (on by default) LuxTunes also:
<ul>
<li>writes your playlists into the iPod's <i>Playlists</i> folder (your own playlists there are
left alone);</li>
<li>gives the iPod's copies of your songs their details and a cover Rockbox can show (a small
plain JPEG, made with FFmpeg). Your library's files are never changed. The first sync checks
every song once, which takes a while; after that only new or edited songs;</li>
<li>adds plays from Rockbox to your library, if Rockbox's <i>Last.fm scrobbler</i> plugin is
on. Its log isn't changed, and no play is counted twice.</li>
</ul>
In Rockbox, use the <b>Database</b> menu to browse by artist and album (the song files have
coded names), and update its database after a sync: Settings → General Settings → Database
→ Initialize Now, or turn on Auto Update there.</li>
</ul>"""),

    ("Syncing an Android phone", "Put your music on your phone", """
<ul>
<li>Unlock the phone, plug it in, and choose <b>File transfer</b> in its USB notification.
It appears under <b>Devices</b>.</li>
<li>Choose internal storage or an SD card, and what to sync, then click <b>Sync</b>. On the
Music tab you can sync your entire library with all playlists or only the ones you
tick, or just chosen playlists, artists, albums, and genres.
Everything goes in one <i>LuxTunes</i> folder that any music player can use.</li>
<li><b>Delete media not in this selection</b> keeps the phone tidy. LuxTunes only ever
touches its own folder.</li>
<li>While it syncs, the phone's <b>Sync</b> button (in the bottom bar) becomes <b>Stop Sync</b>. It stops
straight away, even partway through a big audiobook or a step like adding album covers;
what's already copied stays, and the next sync carries on from there.</li>
<li>If the phone stops responding partway through a file, LuxTunes gives up on that file
after a while (instead of waiting forever), stops the sync, and says so. Unlock the phone
and sync again. Don't unplug the phone while it's still syncing.</li>
<li>Keep the phone unlocked with its screen on during a big sync: some phones slow down or
pause file transfers when the screen turns off.</li>
<li>Syncing to an SD card that keeps failing partway through big files (audiobooks,
movies)? The card may be worn out or counterfeit; LuxTunes says so when it sees this.
Choose the phone's internal storage under <b>Sync to</b> on its Summary, and test the card
on a computer.</li>
<li>Each sync keeps a log of what it did, file by file. If something goes wrong,
<b>Help → Show Sync Logs</b> opens the folder.</li>
<li>With the LuxTunes app for Android on the phone (luxtunes-mobile on GitHub), plays,
ratings, playlists, and the equalizer come back at the next sync. Songs you add on the phone are copied into your
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

    ("Views and finding things", "List, Album, Grid, Carousel", """
<ul>
<li>Switch views with the buttons at the top right or <b>View</b> (Ctrl+Shift+3 to 6).
<b>Album</b> groups each album's songs beside its art; <b>Carousel</b> shows the covers above
the list (drag the divider to make them bigger or smaller), and follows a shuffle to the
album playing.
<b>View Options…</b> (Ctrl+J) and a right-click on the column headers choose columns.</li>
<li>Click a column header to sort by it. Each Library section and playlist keeps its own
sort, and where you'd scrolled to while LuxTunes is open.</li>
<li>Type in the search box to filter what's shown. Accents and capitals don't matter:
<i>lumen</i> finds <i>Lümen</i> and <i>sigur ros</i> finds <i>Sigur Rós</i>. Click the
magnifier to search only one kind of tag: Artist (which also covers Album Artist,
Artists, and Featured Artist), Album, Composer, or Song (All searches them all). It goes
back to All each time LuxTunes starts.</li>
<li>Each Library section (Music, Movies, TV Shows…) keeps its own columns and their
order; playlists use Music's.</li>
<li><b>File → Display Duplicates</b> and <b>Display Missing</b> help clean up;
<b>Display All</b> in the bottom bar goes back.</li>
</ul>"""),

    ("Podcasts", "Subscribe to podcasts and get new episodes", """
<ul>
<li><b>File → Subscribe to Podcast…</b> and paste the podcast's feed address (the "RSS" link on
its website). No account is needed. Its newest episode is downloaded into your LuxTunes Media
folder (Podcasts/&lt;podcast&gt;) and shows up under Podcasts.</li>
<li><b>File → Refresh Podcasts</b> checks every podcast and downloads the episodes that are new
since the last check. LuxTunes asks before going online, unless you allow it in
<b>Preferences → Network</b>, where podcasts can also be refreshed when LuxTunes starts.</li>
<li><b>File → Podcasts…</b> lists every episode each podcast offers: select older ones and click
<b>Download</b>, or <b>Unsubscribe</b> (the episodes you downloaded stay).</li>
<li>In the Podcasts section, <b>Subscribe…</b>, <b>Refresh</b> and <b>Podcasts…</b> are in the
bottom bar too.</li>
<li>An episode you delete isn't downloaded again. Only secure (https://) addresses are used.</li>
</ul>"""),

    ("Updates and privacy", "Nothing goes online unless you allow it", """
<ul>
<li>LuxTunes only goes online when you agree: CD names, Get Track Info Online…, Get Lyrics
Online…, podcasts, and updates. The bottom bar (where the library's totals are) says
whenever it connects.</li>
<li><b>Preferences → Network</b> lets each of those go ahead without asking (all off at
first; turning one on tells you what it sends, and to whom). Updates can be checked, and
podcasts refreshed, when LuxTunes starts. Each connection only lasts as long as that one
request; LuxTunes never accepts connections from outside.</li>
<li><b>Help → Check for Updates…</b> looks for a new version (after asking, or by itself at
startup if Preferences → Network allows it) and offers to install it; nothing is installed
without your yes. Every download is checked against the checksum GitHub publishes for it
before it's installed.</li>
</ul>"""),
]
