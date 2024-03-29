from asyncio import subprocess
import datetime
from typing import final
import requests
import os.path
import json
# pip install ffmpeg-python
import ffmpeg
import subprocess
# pip install python-slugify
from slugify import slugify
from bs4 import BeautifulSoup
from pytube import YouTube
# pip install youtube-search-python
from youtubesearchpython import VideosSearch


# print("Running sitewatcher.py")

# Settings
videoSavePath = "C:\\Github-Projects\\Python Music Video Grabber\\videos\\"
additional_search_text = " official video"
youtube_search_url_base = "https://youtube.com/results?search_query="
json_filename = 'songs.json'
URL="https://xmplaylist.com/station/altnation"
CREATE_NO_WINDOW = 0x08000000

# Class strings from the html

# line 160
song_elements_class = "flex flex-row overflow-hidden rounded-xl shadow-sm"

# line 178 in sample.html
song_anchor_class = "focus:shadow-outline-blue inline-flex items-center rounded border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium leading-4 text-gray-700 transition duration-150 ease-in-out hover:border-gray-400 hover:text-gray-800 focus:border-blue-300 focus:outline-none active:bg-gray-50 active:text-gray-800"

# line 169
song_title_class = "mt-2 text-lg font-semibold leading-5 text-gray-900 md:text-xl md:leading-6 lg:text-lg xl:text-xl"

# line 170
artist_class = "mt-1 text-sm text-gray-500 md:text-base md:leading-6 lg:text-sm xl:text-base"

# Retrieve the current database of saved songs
with open(json_filename) as json_file:
    json_songs = json.load(json_file)

# Check the xmplaylist.com for recently played songs
page = requests.get(URL)
# print(str(page.status_code))
soup = BeautifulSoup(page.content, "html.parser")
# print(str(soup.contents))

# Get the song information
song_elements = soup.find_all("div", class_=song_elements_class) # line 157 in sample.html
# print(song_elements)
for song_element in song_elements:
    search_terms = ""
    songTitle = ""
    youtube_search_url = youtube_search_url_base
    # print ("Song element: " + str(song_element))

    songAnchor = song_element.find("a", class_ = song_anchor_class)

    # the song anchor is a unique key for each song. If it is already in the
    # database, then we are done with this song
    songId = songAnchor['href']
    # print("Song ID: " + songId)
    if songId in json_songs['songs'].keys():
        # print (songId + " already added to database")
        continue

    # if we are here, then it must be a new song
    # print("Looks like this is a new song!")
    json_songs['songs'][songId] = {}

    # Get the song title
    title_element = song_element.find("h3", class_=song_title_class)
    if (title_element is not None):
    #   print("title: " + title_element.text)
      songTitle = title_element.text.strip()
      search_terms += songTitle
      json_songs['songs'][songId]['song-title'] = songTitle

    # get the list of artists
    artists = song_element.find("ul", class_=artist_class)
    if (artists is not None):
        # print(artists)
        search_terms += " "
        artistList = ""

        for li in artists.find_all("li"):
            # print("artist: " + li.text, end=" ")
            artistList += li.text.strip() + " "
            # print(artistList)
    
    json_songs['songs'][songId]['song-artist'] = artistList.strip()

    # to do the youtube search, look for the artists, song title and any
    # additional search terms, such as 'official video'
    search_terms += artistList
    search_terms += additional_search_text
    # print (search_terms)
    
    # Search youtube for the video. Feeling lucky that the first hit will be
    # the best video.
    videosearch = VideosSearch(search_terms, limit=1)
    videoUrl = videosearch.result()["result"][0]["link"]
    json_songs['songs'][songId]['video-url'] = videoUrl
    # print(videoUrl)

    # get the information about the video
    yt = YouTube(videoUrl)
    videoTitle = yt.title
    json_songs['songs'][songId]['video-title'] = videoTitle
    videoAuthor = yt.author
    json_songs['songs'][songId]['video-author'] = videoAuthor

    videoYear = yt.publish_date.year
    json_songs['songs'][songId]['video-publish-date'] = str(yt.publish_date)

    # create the filename for the saved video
    filename = slugify(artistList, lowercase=False, separator=" ") + " - " + \
        slugify(songTitle, lowercase=False, separator=" ") + \
        " (" + str(videoYear) + ").mp4"
    json_songs['songs'][songId]['video-filename'] = filename
    # print(filename)


    # save the video (only if it isn't already saved)
    if (not os.path.isfile(videoSavePath + filename)):
        # print("Saving " + videoSavePath + filename)
        try:
            os.remove("video.mp4")
            os.remove("audio.mp4")
        except:
            pass
        
        sucessfulDownload = False
        try:
            video = yt.streams.filter(res="1080p").first().download()
            os.rename(video,"video.mp4")
            sucessfulDownload = True
        except Exception as err:
            json_songs['songs'][songId]['1080p-video-download-error'] = str(err)
            # print(f"There was an error downloading 1080p video {err=}, {type(err)=}")
        
        if (sucessfulDownload == False):
            try:
                video = yt.streams.filter(res="720p").first().download()
                os.rename(video,"video.mp4")
                sucessfulDownload = True
            except Exception as err:
                json_songs['songs'][songId]['720p-video-download-error'] = str(err)
                # print(f"There was an error downloading 720p video {err=}, {type(err)=}")

        if (sucessfulDownload == False):
            try:
                video = yt.streams.get_highest_resolution().download()
                os.rename(video,"video.mp4")
                sucessfulDownload = True
            except Exception as err:
                json_songs['songs'][songId]['video-download-error'] = str(err)
                # print(f"There was an error downloading highest resolution video {err=}, {type(err)=}")

        try:
            audio = yt.streams.filter(only_audio=True).first().download()
            os.rename(audio,"audio.mp4")
            video_stream = ffmpeg.input('video.mp4')
            audio_stream = ffmpeg.input('audio.mp4')

            # ffmpeg.output(audio_stream, video_stream, videoSavePath + filename).run()
            # ffmpeg.output(audio_stream, video_stream, videoSavePath + filename).global_args('-loglevel', 'quiet').run()
            subprocess.run([
                'ffmpeg',
                '-i', 'video.mp4',
                '-i', 'audio.mp4',
                '-c:v', 'copy',
                '-c:a', 'aac',
                '-loglevel', 'quiet',
                videoSavePath + filename
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)

        except Exception as err:
            json_songs['songs'][songId]['video-download-error'] = str(err)
            # print(f"There was an error {err=}, {type(err)=}")
            continue
    # else:
    #     print (filename + " already downloaded")

    json_songs['songs'][songId]['video-download-full-filename'] = videoSavePath + filename
    json_songs['songs'][songId]['video-download-datetime'] = str(datetime.datetime.now())

# Update the json file
# print ("Almost done. Writing out the json file now")
with open(json_filename, 'w') as out_file:
    json.dump(json_songs, out_file, indent=4)
# print ("All done! Enjoy the videos")
