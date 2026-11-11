"""The iPod nano 6th and 7th generation: what libgpod needs to write for them,
and what LuxTunes changes afterwards so the nano accepts the library.

Taken from each nano's SysInfoExtended (its own description of itself, read
over USB) and from the library iTunes leaves on it:
- the SQL the nano expects to have been run after its library is written
  (its "post-process commands"; libgpod only runs them when a SysInfoExtended
  file is on the iPod, which these nanos don't have),
- columns iTunes' library has that libgpod's doesn't,
- the version numbers iTunes writes,
- the artwork sizes the nano uses (sizes iTunes doesn't write are left out).
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass

# libgpod's own additions, which iTunes doesn't leave on these nanos.
LIBGPOD_EXTRAS = (
    "DROP TRIGGER IF EXISTS insert_item",
    "DROP TRIGGER IF EXISTS update_item_media_kind",
    "DROP TABLE IF EXISTS item_to_album",
    "DROP TABLE IF EXISTS item_to_artist",
    "DROP TABLE IF EXISTS item_to_composer",
    'DROP TABLE IF EXISTS "Extras.itdb".db_schema_upgrade_info',
)


@dataclass(frozen=True)
class Nano:
    commands: tuple[str, ...]           # names in SQL, in the order they're run
    missing_columns: tuple[tuple[str, str], ...]
    library_version: tuple[int, int]    # version_info (major, minor)
    user_versions: dict                 # file -> PRAGMA user_version
    song_list_version: int              # iTunesCDB header
    family_id: int
    album_art: list
    photos: list
    chapter_images: list

    def sql(self):
        return [(name, SQL[name]) for name in self.commands]

    def sysinfo_extended(self, firewire_guid: str) -> bytes:
        """A SysInfoExtended for libgpod. DBVersion 4 has libgpod sign with
        hash72 (LuxTunes then signs with hashAB, which libgpod can't make)."""
        return plistlib.dumps({
            "FireWireGUID": firewire_guid,
            "SerialNumber": "",         # too short to look up, so libgpod uses ModelNumStr quietly
            "FamilyID": self.family_id,
            "DBVersion": 4,
            "SQLiteDB": True,
            "PodcastsSupported": True,
            "VoiceMemosSupported": True,
            "PlaylistFoldersSupported": True,
            "SortFieldsSupported": True,
            "SupportsSparseArtwork": True,
            "AlbumArt2": self.album_art,
            "ImageSpecifications2": self.photos,
            "ChapterImageSpecs2": self.chapter_images,
        })

    @property
    def art_sizes(self) -> set[int]:
        return {f["FormatId"] for f in self.album_art}


SQL = {
    'DropTrackArtist':
        'DROP TABLE IF EXISTS track_artist;',
    'CreateTrackArtist':
        'CREATE TABLE track_artist (pid INTEGER NOT NULL, name TEXT, name_order INTEGER, sort_name TEXT, has_songs INTEGER DEFAULT 0, has_music_videos INTEGER DEFAULT 0, has_non_compilation_tracks INTEGER DEFAULT 0, PRIMARY KEY (pid));',
    'PopulateTrackArtist':
        'INSERT INTO track_artist ( name, sort_name, name_order ) SELECT DISTINCT artist, sort_artist, artist_order FROM item GROUP BY artist_order;',
    'AddItemTrackArtist':
        'ALTER TABLE item ADD COLUMN track_artist_pid INTEGER DEFAULT 0;',
    'LinkItemTrackArtist':
        'UPDATE item SET track_artist_pid = (SELECT pid FROM track_artist WHERE item.artist_order = track_artist.name_order AND item.artist IS NOT NULL);',
    'CreateIndexForItemTrackArtistPid':
        'CREATE INDEX idx_item_track_artist_pid ON item (track_artist_pid);',
    'CreateTempIndexForItemArtistPid':
        'CREATE INDEX idx_temp_item_artist_pid ON item (artist_pid);',
    'FixUnknownAudioBookAlbumInItem':
        'UPDATE item SET album_pid = (SELECT album.pid FROM album WHERE item.artist_pid = album.artist_pid AND album.name IS NULL) WHERE is_audio_book = 1 AND album IS NULL;',
    'FixUnknownAudioBookAlbumInAlbum':
        'UPDATE album SET name = (SELECT item.title FROM item WHERE item.artist_pid = album.artist_pid AND item.is_audio_book = 1 AND item.album IS NULL) WHERE name IS NULL;',
    'AddTrackArtistIsUnknown':
        'ALTER TABLE track_artist ADD COLUMN is_unknown INTEGER DEFAULT 0;',
    'CreateUnknownTrackArtist':
        "INSERT INTO track_artist (name, name_order, sort_name, is_unknown) SELECT 'Unknown Artist', 4294967295, 'Unknown Artist', 1 FROM item WHERE artist IS NULL GROUP BY artist;",
    'FixUnknownTrackArtists':
        'UPDATE item SET track_artist_pid = (SELECT pid FROM track_artist WHERE track_artist.pid == item.track_artist_pid AND track_artist.name IS NOT NULL);',
    'LinkUnknownTrackArtist':
        'UPDATE item SET track_artist_pid = (SELECT pid FROM track_artist WHERE is_unknown == 1) WHERE track_artist_pid IS NULL;',
    'UpdateArtistHasNonCompilationTracks':
        'UPDATE track_artist SET has_non_compilation_tracks = (SELECT item.is_compilation FROM item WHERE item.track_artist_pid == track_artist.pid AND item.is_compilation = 0 AND (item.is_song=1 OR item.is_music_video=1) LIMIT 1) IS NOT NULL;',
    'AddArtistIsUnknown':
        'ALTER TABLE artist ADD COLUMN is_unknown INTEGER DEFAULT 0;',
    'CreateUnknownArtist':
        "INSERT INTO artist (kind, artwork_status, artwork_album_pid, name, name_order, sort_name, is_unknown) SELECT 2, 0, 0, 'Unknown Artist', 4294967295, 'Unknown Artist', 1 FROM item WHERE artist IS NULL GROUP BY artist ;",
    'FixUnknownArtists':
        'UPDATE item SET artist_pid = (SELECT artist_pid FROM artist WHERE artist.pid == item.artist_pid AND artist.name IS NOT NULL);',
    'LinkUnknownArtist':
        'UPDATE item SET artist_pid = (SELECT pid FROM artist WHERE is_unknown == 1) WHERE artist_pid IS NULL;',
    'AddAlbumIsUnknown':
        'ALTER TABLE album ADD COLUMN is_unknown INTEGER DEFAULT 0;',
    'CreateUnknownAlbum':
        "INSERT INTO album (name, name_order, kind, artist_pid, artwork_status, artwork_item_pid, user_rating, all_compilations, season_number, is_unknown) SELECT 'Unknown Album', 4294967295, 2, (SELECT pid FROM artist WHERE is_unknown==1), 0, 0, 0, 0, 0, 1 FROM item WHERE album IS NULL GROUP BY album;",
    'FixUnknownAlbums':
        'UPDATE item SET album_pid = (SELECT album_pid FROM album WHERE album.pid == item.album_pid AND album.name IS NOT NULL);',
    'LinkUnknownAlbum':
        'UPDATE item SET album_pid = (SELECT pid FROM album WHERE is_unknown == 1) WHERE album_pid IS NULL;',
    'CreateIndexForItemAlbumPid':
        'CREATE INDEX idx_item_album_pid ON item (album_pid);',
    'AddGenreIsUnknown':
        'ALTER TABLE genre_map ADD COLUMN is_unknown INTEGER DEFAULT 0;',
    'CreateUnknownGenre':
        "INSERT INTO genre_map (genre, genre_order, is_unknown) SELECT 'Unknown Genre', 4294967295, 1 FROM item WHERE genre_id = 0 GROUP BY genre_id;",
    'LinkUnknownGenre':
        'UPDATE item SET genre_id = (SELECT id FROM genre_map WHERE is_unknown == 1) WHERE genre_id == 0;',
    'AddComposerIsUnknown':
        'ALTER TABLE composer ADD COLUMN is_unknown INTEGER DEFAULT 0;',
    'CreateUnknownComposer':
        "INSERT INTO composer (name, name_order, sort_name, is_unknown) SELECT 'Unknown Composer', 4294967295, 'Unknown Composer', 1 FROM item WHERE composer IS NULL GROUP BY composer;",
    'LinkUnknownComposer':
        'UPDATE item SET composer_pid = (SELECT pid FROM composer WHERE is_unknown == 1) WHERE composer_pid == 0 ;',
    'AddAlbumArtistHasSongs':
        'ALTER TABLE artist ADD COLUMN has_songs INTEGER DEFAULT 0;',
    'UpdateAlbumArtistHasSongs':
        'UPDATE artist SET has_songs = (SELECT item.is_song FROM item WHERE item.artist_pid == artist.pid AND item.is_song = 1 LIMIT 1) IS NOT NULL;',
    'UpdateArtistHasSongs':
        'UPDATE track_artist SET has_songs = (SELECT item.is_song FROM item WHERE item.track_artist_pid == track_artist.pid AND item.is_song = 1 LIMIT 1) IS NOT NULL;',
    'AddAlbumArtistHasMusicVideos':
        'ALTER TABLE artist ADD COLUMN has_music_videos INTEGER DEFAULT 0;',
    'UpdateAlbumArtistHasMusicVideos':
        'UPDATE artist SET has_music_videos = (SELECT item.is_music_video FROM item WHERE item.artist_pid == artist.pid AND item.is_music_video = 1 LIMIT 1) IS NOT NULL;',
    'UpdateArtistHasMusicVideos':
        'UPDATE track_artist SET has_music_videos = (SELECT item.is_music_video FROM item WHERE item.track_artist_pid == track_artist.pid AND item.is_music_video = 1 LIMIT 1) IS NOT NULL;',
    'AddAlbumHasSongs':
        'ALTER TABLE album ADD COLUMN has_songs INTEGER DEFAULT 0;',
    'InsertAlbumHasSongs':
        'UPDATE album SET has_songs = (SELECT item.is_song FROM item WHERE item.album_pid == album.pid AND item.is_song == 1 LIMIT 1) IS NOT NULL;',
    'AddAlbumHasMusicVideos':
        'ALTER TABLE album ADD COLUMN has_music_videos INTEGER DEFAULT 0;',
    'InsertAlbumHasMusicVideos':
        'UPDATE album SET has_music_videos = (SELECT item.is_music_video FROM item WHERE item.album_pid == album.pid AND item.is_music_video == 1 LIMIT 1) IS NOT NULL;',
    'AddAlbumSortAlbum':
        'ALTER TABLE album ADD COLUMN sort_order INTEGER DEFAULT 0;',
    'InsertAlbumSortAlbum':
        'UPDATE album SET sort_order = (SELECT item.album_order FROM item WHERE item.album_pid == album.pid LIMIT 1);',
    'AddAlbumArtistOrder':
        'ALTER TABLE album ADD COLUMN artist_order INTEGER DEFAULT 0;',
    'InsertAlbumArtistOrder':
        'UPDATE album SET artist_order = (SELECT name_order FROM artist WHERE album.artist_pid = artist.pid LIMIT 1);',
    'AddAlbumAnyCompilation':
        'ALTER TABLE album ADD COLUMN has_any_compilations INTEGER DEFAULT 0;',
    'UpdateAlbumAnyCompilation':
        'UPDATE album SET has_any_compilations = (SELECT item.is_compilation FROM item WHERE item.album_pid == album.pid AND item.is_compilation == 1 LIMIT 1) IS NOT NULL;',
    'AddGenreHasMusic':
        'ALTER TABLE genre_map ADD COLUMN has_music INTEGER DEFAULT 0;',
    'InsertGenreHasMusic':
        'UPDATE genre_map SET has_music =  (SELECT is_song FROM item WHERE item.genre_id = genre_map.id AND item.media_kind & 33 LIMIT 1) IS NOT NULL;',
    'AddComposerHasMusic':
        'ALTER TABLE composer ADD COLUMN has_music INTEGER DEFAULT 0;',
    'InsertComposerHasMusic':
        'UPDATE composer SET has_music =  (SELECT is_song FROM item WHERE item.composer_pid = composer.pid AND item.media_kind & 33 LIMIT 1) IS NOT NULL;',
    'CreateItemToContainerTempIndex':
        'CREATE INDEX item_to_container_temp_idx ON item_to_container (item_pid, container_pid);',
    'AddFallbackSortOrderForItem':
        'ALTER TABLE item ADD COLUMN physical_order INTEGER;',
    'PopulateFallbackSortOrderForItem':
        'UPDATE item SET physical_order = (SELECT physical_order FROM item_to_container WHERE container_pid == (SELECT primary_container_pid FROM db_info LIMIT 1) AND item_pid = item.pid);',
    'DropItemToContainerTempIndex':
        'DROP INDEX item_to_container_temp_idx;',
    'AddTrackArtistAlbumCount':
        'ALTER TABLE track_artist ADD COLUMN album_count INTEGER DEFAULT 0;',
    'PopulateTrackArtistAlbumCount':
        'UPDATE track_artist SET album_count = (select COUNT(DISTINCT(album_pid)) FROM item WHERE item.track_artist_pid == track_artist.pid);',
    'CreateRentalExpiredColumn':
        'ALTER TABLE item_stats ADD COLUMN rental_expired INTEGER DEFAULT 0;',
    'CreateAlbumSortName':
        'ALTER TABLE album ADD COLUMN sort_name TEXT;',
    'UpdateAlbumSortName':
        'UPDATE album SET sort_name = (SELECT DISTINCT sort_album from item WHERE album.pid = item.album_pid LIMIT 1);',
    'AddItemHasLyrics':
        'ALTER TABLE item ADD COLUMN has_lyrics INTEGER DEFAULT 0;',
    'UpdateItemHasLyrics':
        'UPDATE item SET has_lyrics =  (SELECT item_pid FROM lyrics WHERE item.pid = lyrics.item_pid) IS NOT NULL;',
    'AddItemDateReleased':
        'ALTER TABLE item ADD COLUMN date_released INTEGER DEFAULT 0;',
    'UpdateItemDateReleased':
        'UPDATE item SET date_released = (SELECT COALESCE(store_info.date_released, podcast_info.date_released, 0) FROM item AS item2 LEFT OUTER JOIN store_info ON item.pid = store_info.item_pid LEFT OUTER JOIN podcast_info ON item.pid = podcast_info.item_pid WHERE item2.pid = item.pid);',
    'FixUnknownTVSeries':
        'UPDATE video_info SET series_name = (SELECT item.title FROM item WHERE video_info.item_pid = item.pid) WHERE video_info.series_name IS NULL;',
    'CreateArtistsForTVSeries':
        'INSERT INTO track_artist(name, sort_name) SELECT DISTINCT video_info.series_name, video_info.sort_series_name FROM video_info, item WHERE item.pid == video_info.item_pid AND item.is_tv_show;',
    'UpdateArtistForTVSeries':
        'UPDATE item SET artist = (SELECT series_name FROM  video_info WHERE item.pid = video_info.item_pid) WHERE item.is_tv_show = 1;',
    'UpdateSortArtistForTVSeries':
        'UPDATE item SET sort_artist = (SELECT sort_series_name FROM  video_info WHERE item.pid = video_info.item_pid) WHERE item.is_tv_show = 1;',
    'UpdateTrackArtistPidForTVSeries':
        'UPDATE item SET track_artist_pid = (SELECT pid FROM track_artist WHERE item.artist = track_artist.name AND track_artist.name_order IS NULL) WHERE item.is_tv_show = 1;',
    'CreatePlayCountUserOriginal':
        'ALTER TABLE item_stats ADD COLUMN play_count_user_original INTEGER DEFAULT 0;',
    'CreateSkipCountUserOriginal':
        'ALTER TABLE item_stats ADD COLUMN skip_count_user_original INTEGER DEFAULT 0;',
    'UpdatePlayCountUserOriginal':
        'UPDATE item_stats SET play_count_user_original = play_count_user;',
    'UpdateSkipCountUserOriginal':
        'UPDATE item_stats SET skip_count_user_original = skip_count_user;',
    'DropTempIndexForItemArtistPid':
        'DROP INDEX idx_temp_item_artist_pid;',
    'CreateArtistSortOrderIndex':
        'CREATE INDEX item_artist_sort_order_idx ON item (artist_order,album_order,disc_number,track_number,sort_title,physical_order);',
    'CreateItemToContainerPhysicalOrderIndex':
        'CREATE INDEX item_to_container_physical_order_idx ON item_to_container (physical_order);',
    'CreateItemToContainerContainerPIDIndex':
        'CREATE INDEX item_to_container_container_pid_idx ON item_to_container (container_pid);',
    'CreateItemTitleOrderIndex':
        'CREATE INDEX item_title_order_idx ON item (title_order);',
    'CreateItemAlbumOrderIndex':
        'CREATE INDEX item_album_order_idx ON item (album_order,disc_number,track_number,artist_order,sort_title,physical_order);',
    'CreateItemGenreIdIndex':
        'CREATE INDEX item_genre_id_idx ON item (genre_id);',
    'DropTrackSize':
        'DROP TABLE IF EXISTS track_size_calc;',
    'CreateTrackSize':
        'CREATE TABLE track_size_calc (pid INTEGER NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL, PRIMARY KEY (pid), UNIQUE (kind));',
    'PopulateTrackSizeAudio':
        "INSERT INTO track_size_calc ( kind, size ) SELECT 'audio' AS kind, COALESCE(SUM(location.file_size), 0) FROM location, item WHERE item.pid = location.item_pid AND item.media_kind & 3146029;",
    'PopulateTrackSizeVideo':
        "INSERT INTO track_size_calc ( kind, size ) SELECT 'video' AS kind, COALESCE(SUM(location.file_size), 0) FROM location, item WHERE item.pid = location.item_pid AND item.media_kind & 3682;",
    'PopulateTrackSizeMusicVideo':
        "INSERT INTO track_size_calc ( kind, size ) SELECT 'music_video' AS kind, COALESCE(SUM(location.file_size), 0) FROM location, item WHERE item.pid = location.item_pid AND item.media_kind & 32;",
    'AddAlbumArtistCount':
        'ALTER TABLE album ADD COLUMN artist_count_calc INTEGER DEFAULT 0 NOT NULL;',
    'UpdateAlbumArtistCount':
        'UPDATE album SET artist_count_calc = COALESCE ((SELECT COUNT(DISTINCT item.artist_pid) FROM item WHERE item.album_pid = album.pid GROUP BY item.album_pid), 0);',
    'AddGenreMapArtistCount':
        'ALTER TABLE genre_map ADD COLUMN artist_count_calc INTEGER DEFAULT 0 NOT NULL;',
    'AddGenreMapAlbumCount':
        'ALTER TABLE genre_map ADD COLUMN album_count_calc  INTEGER DEFAULT 0 NOT NULL;',
    'AddGenreMapCompilationCount':
        'ALTER TABLE genre_map ADD COLUMN compilation_count_calc  INTEGER DEFAULT 0 NOT NULL;',
    'UpdateGenreMapArtistCount':
        'UPDATE genre_map SET artist_count_calc = COALESCE((SELECT COUNT(DISTINCT item.track_artist_pid) FROM item WHERE item.media_kind & 33 AND item.genre_id = genre_map.id GROUP BY item.genre_id), 0);',
    'UpdateGenreMapAlbumCount':
        'UPDATE genre_map SET album_count_calc  = COALESCE((SELECT COUNT(DISTINCT item.album_pid) FROM item WHERE item.media_kind & 33 AND item.genre_id = genre_map.id GROUP BY item.genre_id), 0);',
    'UpdateGenreMapCompilationCount':
        'UPDATE genre_map SET compilation_count_calc = COALESCE((SELECT COUNT(DISTINCT item.album_pid) FROM item WHERE item.is_compilation = 1 AND item.media_kind & 33 AND item.genre_id = genre_map.id GROUP BY item.genre_id), 0);',
    'AddAlbumHasMovies':
        'ALTER TABLE album ADD COLUMN has_movies INTEGER DEFAULT 0;',
    'InsertAlbumHasMovies':
        'UPDATE album SET has_movies = (SELECT item.is_movie FROM item WHERE item.album_pid == album.pid AND item.is_movie == 1 LIMIT 1) IS NOT NULL;',
    'AddAlbumItemCount':
        'ALTER TABLE album ADD COLUMN item_count INTEGER DEFAULT 0;',
    'PopulateAlbumItemCount':
        'UPDATE album SET item_count = (SELECT COUNT(item.pid) FROM item WHERE item.album_pid == album.pid);',
    'DropItemTitleOrderIndex':
        'DROP INDEX item_title_order_idx;',
    'RecreateItemTitleOrderIndex':
        'CREATE INDEX item_title_order_idx ON item (title_order, media_kind);',
    'CreateItemComposerOrderIndex':
        'CREATE INDEX IF NOT EXISTS item_composer_order_idx ON item (composer_pid, composer_order, media_kind);',
    'DropItemToContainerContainerPIDIndex':
        'DROP INDEX item_to_container_container_pid_idx;',
    'RecreateItemToContainerContainerPIDIndex':
        'CREATE INDEX item_to_container_container_pid_idx ON item_to_container (container_pid, physical_order, item_pid);',
    'AddItemStatsGeniusId':
        'ALTER TABLE item_stats ADD COLUMN genius_id INTEGER DEFAULT 0;',
    'UpdateItemStatsGeniusId':
        'UPDATE item_stats SET genius_id = (SELECT genius_id FROM item WHERE item.pid == item_stats.item_pid AND item.genius_id IS NOT NULL);',
    'UpdateDeviceUpdateLevelTo1104':
        'UPDATE version_info SET device_update_level = 1104;',
    'AddAlbumVolumeNormEnergy':
        'ALTER TABLE album ADD COLUMN min_volume_normalization_energy INTEGER DEFAULT 0;',
    'UpdateAlbumVolumeNormEnergy':
        'UPDATE album SET min_volume_normalization_energy = (select min(avformat_info.volume_normalization_energy) from avformat_info, item where item.pid = avformat_info.item_pid and item.album_pid = album.pid group by item.album_pid);',
    'AddAlbumArtistHasNonCompilationTracks':
        'ALTER TABLE artist ADD COLUMN has_non_compilation_tracks INTEGER DEFAULT 0;',
    'UpdateAlbumArtistHasNonCompilationTracks':
        'UPDATE artist SET has_non_compilation_tracks = (SELECT item.is_compilation FROM item WHERE item.artist_pid == artist.pid AND item.is_compilation = 0 AND (item.is_song=1 OR item.is_music_video=1) LIMIT 1) IS NOT NULL;',
    'AddAlbumArtistAlbumCount':
        'ALTER TABLE artist ADD COLUMN album_count INTEGER DEFAULT 0;',
    'PopulateAlbumArtistAlbumCount':
        'UPDATE artist SET album_count = (select COUNT(DISTINCT(album_pid)) FROM item WHERE item.artist_pid == artist.pid);',
    'AddGenreMapAlbumArtistCount':
        'ALTER TABLE genre_map ADD COLUMN album_artist_count_calc INTEGER DEFAULT 0 NOT NULL;',
    'UpdateGenreMapAlbumArtistCount':
        'UPDATE genre_map SET album_artist_count_calc = COALESCE((SELECT COUNT(DISTINCT item.artist_pid) FROM item WHERE item.media_kind & 33 AND item.genre_id = genre_map.id GROUP BY item.genre_id), 0);',
    'CreateArtistNameOrderIndex':
        'CREATE INDEX artist_name_order_idx ON artist (name_order, has_songs, has_music_videos);',
    'CreateAlbumArtistSortOrderAudioIndex':
        'CREATE INDEX album_artist_order_audio_idx ON album (artist_order, sort_order, has_songs, has_music_videos);',
    'CreateAlbumSortOrderAudioIndex':
        'CREATE INDEX album_sort_order_audio_idx ON album (sort_order, has_songs, has_music_videos);',
    'CreateAlbumSortOrderAudioCompilationIndex':
        'CREATE INDEX album_name_order_audio_compilations_idx ON album (sort_order, has_any_compilations, has_songs, has_music_videos);',
    'AddItemStatsAudioLanguage':
        'ALTER TABLE item_stats ADD COLUMN audio_language INTEGER;',
    'AddItemStatsSubtitleLanguage':
        'ALTER TABLE item_stats ADD COLUMN subtitle_language INTEGER;',
    'AddItemStatsAudioTrackID':
        'ALTER TABLE item_stats ADD COLUMN audio_track_id INTEGER;',
    'AddItemStatsSubtitleTrackID':
        'ALTER TABLE item_stats ADD COLUMN subtitle_track_id INTEGER;',
    'UpdateItemStatsAudioLanguage':
        'UPDATE item_stats SET audio_language = (select audio_language FROM video_info WHERE video_info.item_pid == item_stats.item_pid);',
    'UpdateItemStatsSubtitleLanguage':
        'UPDATE item_stats SET subtitle_language = (select subtitle_language FROM video_info WHERE video_info.item_pid == item_stats.item_pid);',
    'UpdateItemStatsAudioTrackID':
        'UPDATE item_stats SET audio_track_id = (select audio_track_id FROM video_info WHERE video_info.item_pid == item_stats.item_pid);',
    'UpdateItemStatsSubtitleTrackID':
        'UPDATE item_stats SET subtitle_track_id = (select subtitle_track_id FROM video_info WHERE video_info.item_pid == item_stats.item_pid);',
    'UpdateDeviceUpdateLevelTo1109':
        'UPDATE version_info SET device_update_level = 1109;',
}

NANOS = {
    # iPod nano 6th generation (firmware 1.2)
    'nano_6': Nano(
        commands=(
            'DropTrackArtist',
            'CreateTrackArtist',
            'PopulateTrackArtist',
            'AddItemTrackArtist',
            'LinkItemTrackArtist',
            'CreateIndexForItemTrackArtistPid',
            'CreateTempIndexForItemArtistPid',
            'FixUnknownAudioBookAlbumInItem',
            'FixUnknownAudioBookAlbumInAlbum',
            'AddTrackArtistIsUnknown',
            'CreateUnknownTrackArtist',
            'FixUnknownTrackArtists',
            'LinkUnknownTrackArtist',
            'UpdateArtistHasNonCompilationTracks',
            'AddArtistIsUnknown',
            'CreateUnknownArtist',
            'FixUnknownArtists',
            'LinkUnknownArtist',
            'AddAlbumIsUnknown',
            'CreateUnknownAlbum',
            'FixUnknownAlbums',
            'LinkUnknownAlbum',
            'CreateIndexForItemAlbumPid',
            'AddGenreIsUnknown',
            'CreateUnknownGenre',
            'LinkUnknownGenre',
            'AddComposerIsUnknown',
            'CreateUnknownComposer',
            'LinkUnknownComposer',
            'AddAlbumArtistHasSongs',
            'UpdateAlbumArtistHasSongs',
            'UpdateArtistHasSongs',
            'AddAlbumArtistHasMusicVideos',
            'UpdateAlbumArtistHasMusicVideos',
            'UpdateArtistHasMusicVideos',
            'AddAlbumHasSongs',
            'InsertAlbumHasSongs',
            'AddAlbumHasMusicVideos',
            'InsertAlbumHasMusicVideos',
            'AddAlbumSortAlbum',
            'InsertAlbumSortAlbum',
            'AddAlbumArtistOrder',
            'InsertAlbumArtistOrder',
            'AddAlbumAnyCompilation',
            'UpdateAlbumAnyCompilation',
            'AddGenreHasMusic',
            'InsertGenreHasMusic',
            'AddComposerHasMusic',
            'InsertComposerHasMusic',
            'CreateItemToContainerTempIndex',
            'AddFallbackSortOrderForItem',
            'PopulateFallbackSortOrderForItem',
            'DropItemToContainerTempIndex',
            'AddTrackArtistAlbumCount',
            'PopulateTrackArtistAlbumCount',
            'CreateRentalExpiredColumn',
            'CreateAlbumSortName',
            'UpdateAlbumSortName',
            'AddItemHasLyrics',
            'UpdateItemHasLyrics',
            'AddItemDateReleased',
            'UpdateItemDateReleased',
            'FixUnknownTVSeries',
            'CreateArtistsForTVSeries',
            'UpdateArtistForTVSeries',
            'UpdateSortArtistForTVSeries',
            'UpdateTrackArtistPidForTVSeries',
            'CreatePlayCountUserOriginal',
            'CreateSkipCountUserOriginal',
            'UpdatePlayCountUserOriginal',
            'UpdateSkipCountUserOriginal',
            'DropTempIndexForItemArtistPid',
            'CreateArtistSortOrderIndex',
            'CreateItemToContainerPhysicalOrderIndex',
            'CreateItemToContainerContainerPIDIndex',
            'CreateItemTitleOrderIndex',
            'CreateItemAlbumOrderIndex',
            'CreateItemGenreIdIndex',
            'DropTrackSize',
            'CreateTrackSize',
            'PopulateTrackSizeAudio',
            'PopulateTrackSizeVideo',
            'PopulateTrackSizeMusicVideo',
            'AddAlbumArtistCount',
            'UpdateAlbumArtistCount',
            'AddGenreMapArtistCount',
            'AddGenreMapAlbumCount',
            'AddGenreMapCompilationCount',
            'UpdateGenreMapArtistCount',
            'UpdateGenreMapAlbumCount',
            'UpdateGenreMapCompilationCount',
            'AddAlbumHasMovies',
            'InsertAlbumHasMovies',
            'AddAlbumItemCount',
            'PopulateAlbumItemCount',
            'DropItemTitleOrderIndex',
            'RecreateItemTitleOrderIndex',
            'CreateItemComposerOrderIndex',
            'DropItemToContainerContainerPIDIndex',
            'RecreateItemToContainerContainerPIDIndex',
            'AddItemStatsGeniusId',
            'UpdateItemStatsGeniusId',
            'UpdateDeviceUpdateLevelTo1104',
        ),
        missing_columns=(
            ('item', 'is_tone INTEGER DEFAULT 0'),
            ('item', 'is_book INTEGER DEFAULT 0'),
            ('item', 'is_itunes_u INTEGER DEFAULT 0'),
            ('item', 'is_digital_booklet INTEGER DEFAULT 0'),
            ('item', 'chosen_by_auto_fill INTEGER DEFAULT 0'),
            ('item', 'collection_description TEXT'),
            ('item', 'copyright TEXT'),
            ('video_info', 'is_hd INTEGER'),
            ('store_info', 'xid TEXT'),
            ('store_info', 'flavor TEXT'),
        ),
        library_version=(1, 74),
        user_versions={'Library.itdb': 25, 'Genius.itdb': 2},
        song_list_version=0x4a,
        family_id=17,
        album_art=[
            {'FormatId': 1085, 'RenderWidth': 88, 'RenderHeight': 88, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': True, 'AlignRowBytes': True, 'BackColor': '00000000', 'processForNTSC': True, 'AssociatedFormat': 0},
            {'FormatId': 1073, 'RenderWidth': 240, 'RenderHeight': 240, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
            {'FormatId': 1074, 'RenderWidth': 50, 'RenderHeight': 50, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
            {'FormatId': 1089, 'RenderWidth': 58, 'RenderHeight': 58, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
        ],
        photos=[
            {'FormatId': 1093, 'RenderWidth': 512, 'RenderHeight': 512, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': False, 'AssociatedFormat': 0},
            {'FormatId': 1092, 'RenderWidth': 80, 'RenderHeight': 80, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': True, 'AlignRowBytes': False, 'AssociatedFormat': 0},
        ],
        chapter_images=[
            {'FormatId': 1073, 'RenderWidth': 240, 'RenderHeight': 240, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
        ],
    ),
    # iPod nano 7th generation (firmware 1.1.2)
    'nano_7': Nano(
        commands=(
            'DropTrackArtist',
            'CreateTrackArtist',
            'PopulateTrackArtist',
            'AddItemTrackArtist',
            'LinkItemTrackArtist',
            'CreateIndexForItemTrackArtistPid',
            'CreateTempIndexForItemArtistPid',
            'FixUnknownAudioBookAlbumInItem',
            'FixUnknownAudioBookAlbumInAlbum',
            'AddTrackArtistIsUnknown',
            'CreateUnknownTrackArtist',
            'FixUnknownTrackArtists',
            'LinkUnknownTrackArtist',
            'UpdateArtistHasNonCompilationTracks',
            'AddArtistIsUnknown',
            'CreateUnknownArtist',
            'FixUnknownArtists',
            'LinkUnknownArtist',
            'AddAlbumIsUnknown',
            'CreateUnknownAlbum',
            'FixUnknownAlbums',
            'LinkUnknownAlbum',
            'CreateIndexForItemAlbumPid',
            'AddGenreIsUnknown',
            'CreateUnknownGenre',
            'LinkUnknownGenre',
            'AddComposerIsUnknown',
            'CreateUnknownComposer',
            'LinkUnknownComposer',
            'AddAlbumArtistHasSongs',
            'UpdateAlbumArtistHasSongs',
            'UpdateArtistHasSongs',
            'AddAlbumArtistHasMusicVideos',
            'UpdateAlbumArtistHasMusicVideos',
            'UpdateArtistHasMusicVideos',
            'AddAlbumHasSongs',
            'InsertAlbumHasSongs',
            'AddAlbumHasMusicVideos',
            'InsertAlbumHasMusicVideos',
            'AddAlbumSortAlbum',
            'InsertAlbumSortAlbum',
            'AddAlbumArtistOrder',
            'InsertAlbumArtistOrder',
            'AddAlbumAnyCompilation',
            'UpdateAlbumAnyCompilation',
            'AddGenreHasMusic',
            'InsertGenreHasMusic',
            'AddComposerHasMusic',
            'InsertComposerHasMusic',
            'CreateItemToContainerTempIndex',
            'AddFallbackSortOrderForItem',
            'PopulateFallbackSortOrderForItem',
            'DropItemToContainerTempIndex',
            'AddTrackArtistAlbumCount',
            'PopulateTrackArtistAlbumCount',
            'CreateRentalExpiredColumn',
            'CreateAlbumSortName',
            'UpdateAlbumSortName',
            'AddItemHasLyrics',
            'UpdateItemHasLyrics',
            'AddItemDateReleased',
            'UpdateItemDateReleased',
            'FixUnknownTVSeries',
            'CreateArtistsForTVSeries',
            'UpdateArtistForTVSeries',
            'UpdateSortArtistForTVSeries',
            'UpdateTrackArtistPidForTVSeries',
            'CreatePlayCountUserOriginal',
            'CreateSkipCountUserOriginal',
            'UpdatePlayCountUserOriginal',
            'UpdateSkipCountUserOriginal',
            'DropTempIndexForItemArtistPid',
            'CreateArtistSortOrderIndex',
            'CreateItemToContainerPhysicalOrderIndex',
            'CreateItemToContainerContainerPIDIndex',
            'CreateItemTitleOrderIndex',
            'CreateItemAlbumOrderIndex',
            'CreateItemGenreIdIndex',
            'DropTrackSize',
            'CreateTrackSize',
            'PopulateTrackSizeAudio',
            'PopulateTrackSizeVideo',
            'PopulateTrackSizeMusicVideo',
            'AddAlbumArtistCount',
            'UpdateAlbumArtistCount',
            'AddGenreMapArtistCount',
            'AddGenreMapAlbumCount',
            'AddGenreMapCompilationCount',
            'UpdateGenreMapArtistCount',
            'UpdateGenreMapAlbumCount',
            'UpdateGenreMapCompilationCount',
            'AddAlbumHasMovies',
            'InsertAlbumHasMovies',
            'AddAlbumItemCount',
            'PopulateAlbumItemCount',
            'DropItemTitleOrderIndex',
            'RecreateItemTitleOrderIndex',
            'CreateItemComposerOrderIndex',
            'DropItemToContainerContainerPIDIndex',
            'RecreateItemToContainerContainerPIDIndex',
            'AddItemStatsGeniusId',
            'UpdateItemStatsGeniusId',
            'AddAlbumVolumeNormEnergy',
            'UpdateAlbumVolumeNormEnergy',
            'AddAlbumArtistHasNonCompilationTracks',
            'UpdateAlbumArtistHasNonCompilationTracks',
            'AddAlbumArtistAlbumCount',
            'PopulateAlbumArtistAlbumCount',
            'AddGenreMapAlbumArtistCount',
            'UpdateGenreMapAlbumArtistCount',
            'CreateArtistNameOrderIndex',
            'CreateAlbumArtistSortOrderAudioIndex',
            'CreateAlbumSortOrderAudioIndex',
            'CreateAlbumSortOrderAudioCompilationIndex',
            'AddItemStatsAudioLanguage',
            'AddItemStatsSubtitleLanguage',
            'AddItemStatsAudioTrackID',
            'AddItemStatsSubtitleTrackID',
            'UpdateItemStatsAudioLanguage',
            'UpdateItemStatsSubtitleLanguage',
            'UpdateItemStatsAudioTrackID',
            'UpdateItemStatsSubtitleTrackID',
            'UpdateDeviceUpdateLevelTo1109',
        ),
        missing_columns=(
            ('item', 'is_home_video INTEGER DEFAULT 0'),
            ('item', 'is_tone INTEGER DEFAULT 0'),
            ('item', 'is_book INTEGER DEFAULT 0'),
            ('item', 'is_itunes_u INTEGER DEFAULT 0'),
            ('item', 'is_digital_booklet INTEGER DEFAULT 0'),
            ('item', 'chosen_by_auto_fill INTEGER DEFAULT 0'),
            ('item', 'collection_description TEXT'),
            ('item', 'copyright TEXT'),
            ('avformat_info', 'channels INTEGER DEFAULT 0'),
            ('video_info', 'is_hd INTEGER'),
            ('store_info', 'xid TEXT'),
            ('store_info', 'flavor TEXT'),
            ('container', 'smart_evaluation_order INTEGER'),
        ),
        library_version=(1, 109),
        user_versions={'Library.itdb': 26, 'Genius.itdb': 2},
        song_list_version=0x6d,
        family_id=18,
        album_art=[
            {'FormatId': 1010, 'RenderWidth': 240, 'RenderHeight': 240, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
            {'FormatId': 1013, 'RenderWidth': 50, 'RenderHeight': 50, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
            {'FormatId': 1015, 'RenderWidth': 58, 'RenderHeight': 58, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
            {'FormatId': 1016, 'RenderWidth': 57, 'RenderHeight': 57, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
        ],
        photos=[
            {'FormatId': 1007, 'RenderWidth': 480, 'RenderHeight': 864, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': False, 'AssociatedFormat': 0},
            {'FormatId': 1005, 'RenderWidth': 80, 'RenderHeight': 80, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': True, 'AlignRowBytes': False, 'AssociatedFormat': 0},
        ],
        chapter_images=[
            {'FormatId': 1010, 'RenderWidth': 240, 'RenderHeight': 240, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': True, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
            {'FormatId': 1019, 'RenderWidth': 101, 'RenderHeight': 57, 'PixelFormat': '4C353635', 'Interlaced': False, 'ColorAdjustment': 0, 'GammaAdjustment': 2.2, 'Crop': False, 'AlignRowBytes': True, 'BackColor': '00000000', 'AssociatedFormat': 0},
        ],
    ),
}
