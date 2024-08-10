import os
from plexapi.media import *
from dotenv import load_dotenv
from plexapi.library import Library
from plexapi.server import PlexServer
from datetime import datetime
from plexapi.mixins import ArtUrlMixin, ArtMixin, PosterMixin

load_dotenv()
plex_token = os.getenv("PLEXTOKEN")

baseurl = 'http://192.168.0.84:32400'
plex = PlexServer(baseurl, plex_token)
music_video_library = plex.library.section('Music Videos')

# When I download music videos, I add the year the video was uploaded to YT at the end of the filename
# It is sort of the same thing as the year the song was released, but it's not exactly the same.
# I then set that date as the "originallyAvailableAt" date. Then I can set playlist filters
# For example, if I only want new songs, from like the last two years, it will be easy to create that.

# Run this video periodically to update the dates

# for video in music_video_library.all():
#     if str(video.originallyAvailableAt.year) != video.title[-5:len(video.title)-1]:
#         print("Editing " + video.title)
#         try:
#             video.edit(**{"originallyAvailableAt.value":video.title[-5:len(video.title)-1]+"-01-01 00:00:00"})
#             video.reload()
#         except:
#             print("Couldn't Edit " + video.title)



vid = music_video_library.get("Young the Giant - Cough Syrup (2011)")
vid.edit(**{"originallyAvailableAt.value":vid.title[-5:len(vid.title)-1]+"-01-01 00:00:00"})
myMix = ArtMixin()
vid.uploadArt(url="https://upload.wikimedia.org/wikipedia/en/a/a3/Young_the_Giant_-_Cough_Syrup.jpg")
vid.uploadPoster(url="https://upload.wikimedia.org/wikipedia/en/a/a3/Young_the_Giant_-_Cough_Syrup.jpg")
vid.edit(**{"thumb.value":"https://upload.wikimedia.org/wikipedia/en/a/a3/Young_the_Giant_-_Cough_Syrup.jpg"})
vid.reload()
# thisvid = music_video_library.get("Arlo Parks - Weightless (2023)")
print(vid.originallyAvailableAt)
print(vid.thumb)