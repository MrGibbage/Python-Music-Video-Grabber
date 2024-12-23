import datetime
import requests
import os.path
import json
# pip install python-slugify
from slugify import slugify
# pip install bs4
from bs4 import BeautifulSoup
# pip install pytube
from pytube import YouTube
# pip install youtube-search-python
from youtubesearchpython import VideosSearch
import smtplib
# pip install python-dotenv
from dotenv import load_dotenv
import os
import logging
from pytube.innertube import _default_clients
import ffmpeg
import downloader

# https://github.com/pytube/pytube/issues/1894#issuecomment-2026951321
_default_clients["ANDROID"]["context"]["client"]["clientVersion"] = "19.08.35"
_default_clients["ANDROID_MUSIC"] = _default_clients["ANDROID"]

def sendNotificationEmail(logger, msg):
    load_dotenv()
    gmail_pass = os.getenv("GMAILPASS")
    gmail_address = os.getenv("GMAILADDRESS")
    # print(gmail_address)
    logger.info("Sending notification email now")
    # Send notificaiton email
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(gmail_address, gmail_pass)
        server.sendmail(gmail_address, gmail_address, msg.encode('utf-8', errors='replace'))
        server.quit()
        # print ("Email Sent")
    except Exception as err:
        logger.info(f"Error sending email {err=}, {type(err)=}")


logger = logging.getLogger(__name__)

# Set this to run weekly, every Saturday at about 2:05 PM. The weekly Top 18
# for Alt Nation is played every Saturday at 1:00PM and lasts about an hour.
FORMAT = '%(asctime)s %(message)s'
logging.basicConfig(filename='python_music_video_downloader.log', level=logging.INFO, format=FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
# logging.basicConfig(format=FORMAT)
logger.info('Started')


msg = "Subject: Music Video Download Report\n\n"

# print("Running sitewatcher.py")

# Settings
videoSavePath = "C:\\Github-Projects\\Python Music Video Grabber\\videos\\"
additional_search_text = " official video"
youtube_search_url_base = "https://youtube.com/results?search_query="
json_songs_filename = 'songs.json'
json_channels_filename = 'channels.json'
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
artist_class = "mt-1 text-sm text-gray-900 md:text-base md:leading-6 lg:text-sm xl:text-base"

# Retrieve the current database of saved songs
with open(json_songs_filename) as json_songs_file:
    json_songs = json.load(json_songs_file)

# with open(json_channels_filename) as json_channels_file:
#     json_channels = json.load(json_channels_file)

try:
    # Check the xmplaylist.com for recently played songs
    page = requests.get(URL)
    # print(str(page.status_code))
    soup = BeautifulSoup(page.content, "html.parser")
    # print(str(soup.contents))
    logger.info("Retrieved data from " + URL)
except:
    logger.info("Could not open " + URL)
    logger.info("Aborting")
    msg += "Could not open " + URL
    sendNotificationEmail(logger, msg)
    exit(1)

# Get the song information
song_elements = soup.find_all("div", class_=song_elements_class) # line 157 in sample.html
# print(song_elements)
if song_elements is None:
    logger.info("song_elements was None. Aborting")
    exit(1)
    
msg += "Found " + str(len(song_elements)) + " song elements\r\n"
logger.info("Found " + str(len(song_elements)) + " song elements")
for idx, song_element in enumerate(song_elements, 1):
    msg += "#" + str(idx) + ": "
    logger.info("Song number " + str(idx))
    search_terms = ""
    songTitle = ""
    youtube_search_url = youtube_search_url_base
    # print ("Song element: ", str(song_element))

    songAnchor = song_element.find("a", class_ = song_anchor_class)
    # print("songAnchor", songAnchor)

    # the song anchor is a unique key for each song. If it is already in the
    # database, then we are done with this song
    songId = songAnchor['href']
    # print("Song ID: " + songId)
    logger.info("songAnchor: " + songAnchor['href'])
    if songId in json_songs['songs'].keys():
        # print (songId + " already added to database")
        logger.info(songId + " is already added to database")
        existingSongTitle = json_songs['songs'][songId]['song-title']
        existingSongArtist = json_songs['songs'][songId]['song-artist']
        # existingFileName = json_songs['songs'][songId]['video-filename']
        logger.info("Existing song title: " + json_songs['songs'][songId]['song-title'])
        logger.info("Existing song artist: " + json_songs['songs'][songId]['song-artist'])
        logger.info("Existing song file name: " + json_songs['songs'][songId]['video-filename'])
        # print(json_songs['songs'][songId]['song-title'])
        msg += existingSongTitle + " by " + existingSongArtist + " already in the database\r\n"
        logger.info("Nothing to do. Skipping to next song.")
        continue

    # if we are here, then it must be a new song
    # print("Looks like this is a new song!")
    json_songs['songs'][songId] = {}

    # Get the song title
    title_element = song_element.find("h3", class_=song_title_class)
    if (title_element is not None):
    #   print("title: " + title_element.text)
      songTitle = title_element.text.strip()
      logger.info("New song title: " + songTitle)
      msg += "**NEW** " + songTitle + " by "
      search_terms += songTitle
      json_songs['songs'][songId]['song-title'] = songTitle
    else:
        # print("title_element is None")
        msg += "Title element was none\r\n"
        logger.info("Title element was none. Skipping to next song.")
        continue

    # get the list of artists
    artists = song_element.find("ul", class_=artist_class)
    if (artists is not None):
        # print("artists is not none")
        # print(artists)
        logger.info("Artists is not None")
        # logger.info("Artist list: " + artists)
        search_terms += " "
        artistList = ""

        for li in artists.find_all("li"):
            # print("artist: " + li.text, end=" ")
            artistList += li.text.strip() + " "
            # print(artistList)
        logger.info("artistList = " + artistList)
        msg += artistList + ".\r\n"
    else:
        # print("artists is None")
        msg += "No artists found\r\n"
        logger.info("No artists found. Aborting this song.")
        continue
    
    json_songs['songs'][songId]['song-artist'] = artistList.strip()

    # to do the youtube search, look for the artists, song title and any
    # additional search terms, such as 'official video'
    search_terms += artistList
    search_terms += additional_search_text
    # print (search_terms)
    
    # Search youtube for the video. Feeling lucky that the first hit will be
    # the best video.
    logger.info("Calling VideoSearch")
    videosearch = VideosSearch(search_terms, limit=1)
    logger.info("Getting videoUrl")
    videoUrl = videosearch.result()["result"][0]["link"] 
    logger.info("Getting youtube_id")
    youtube_id = videosearch.result()["result"][0]["id"]
    logger.info("updating json_songs")
    json_songs['songs'][songId]['video-url'] = videoUrl
    # print(videoUrl)

    # create the filename for the saved video
    # Call the downloader method
    try:
        msg += downloader.downloadFromYoutube(videoUrl, json_songs, songId, logger, artistList, songTitle)
    except Exception as e:
        msg += f"There was a problem downloading the file\r\n{e}\r\n"

    headers = {
    'Authorization': os.getenv("AUTHORIZATION"),
    'Content-Type': 'application/json',
    }

    json_data = {
        'data': [
            {
                'youtube_id': youtube_id,
                'status': 'pending',
            },
        ],
    }
    msg += "Trying to add the video to tubearchivist\r\n"
    try:
        print(youtube_id)
        print(json_data)
        response = requests.post(os.getenv("TUBEARCHURL"), headers=headers, json=json_data)
        msg += "Video was added. Check " + os.getenv("TUBEARCHURL") + " for new downloads\r\n"
    except Exception as e:
        msg += f"Could not add video to tube archivist\r\n{e}\r\n"

# Update the json file
# print ("Almost done. Writing out the json file now")
logger.info("Almost done. Writing out the json file now")
with open(json_songs_filename, 'w') as out_file:
    json.dump(json_songs, out_file, indent=4)
# print ("All done! Enjoy the videos")

try:
    os.remove("vid.mp4")
except:
    pass
try:
    os.remove("aud.mp4")
except:
    pass
try:
    os.remove("out.mp4")
except:
    pass

sendNotificationEmail(logger, msg)
logger.info('Finished')
exit(0)