# Set this to run weekly, every Saturday at about 2:05 PM. The weekly Top 18
# for Alt Nation is played every Saturday at 1:00PM and lasts about an hour.
#
# To create an executable file, run
# .venv\Scripts\pyinstaller.exe --noconsole -F sitewatcher.py
# Then copy the sitewatcher.exe file from dist to the 
# python-music-video-grabber folder
#
import json
import logging
from logging.handlers import RotatingFileHandler
import os.path
import os
import smtplib
import shutil
from datetime import datetime

# pip install bs4
from bs4 import BeautifulSoup
# pip install python-dotenv
from dotenv import load_dotenv
# pip install pytube (see comments below)
# from pytube.innertube import _default_clients
# pip install requests
import requests
# pip install youtube-search-python
from youtubesearchpython import VideosSearch
# pip install yt-dlp
import yt_dlp
# pip install spotipy
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

import argparse

# I added this before I switched to yt-dlp. I don't think I need it any more
# but I am keeping it here just in case.

# https://github.com/pytube/pytube/issues/1894#issuecomment-2026951321
# _default_clients["ANDROID"]["context"]["client"]["clientVersion"] = "19.08.35"
# _default_clients["ANDROID_MUSIC"] = _default_clients["ANDROID"]



logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
additional_search_text = " official video"
youtube_search_url_base = "https://youtube.com/results?search_query="
# Class strings from the html

# line 160
song_elements_class = "flex flex-row overflow-hidden rounded-xl shadow-sm"

# line 178 in sample.html
song_anchor_class = "focus:shadow-outline-blue inline-flex items-center rounded border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium leading-4 text-gray-700 transition duration-150 ease-in-out hover:border-gray-400 hover:text-gray-800 focus:border-blue-300 focus:outline-none active:bg-gray-50 active:text-gray-800"

# line 169
song_title_class = "mt-2 text-lg font-semibold leading-5 text-gray-900 md:text-xl md:leading-6 lg:text-lg xl:text-xl"

# line 170
artist_class = "mt-1 text-sm text-gray-900 md:text-base md:leading-6 lg:text-sm xl:text-base"




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
        server.sendmail(gmail_address, gmail_address, f'There was an error sending the message: {err}')
        server.quit()
        logger.info(f"Error sending email {err=}, {type(err)=}")


def get_song_release_date(sp, song_name, artist_name=None):
    query = f'track:{song_name}'
    if artist_name:
        query += f' artist:{artist_name}'
    
    results = sp.search(q=query, type='track', limit=1)
    if results['tracks']['items']:
        track = results['tracks']['items'][0]
        release_date = track['album']['release_date']
        return release_date
    else:
        return "Song not found."

def run(channel: str, save_dir:str):
    '''
    channel is the last part of the url from xmplaylist.com
    save_dir is the directory where the file will be saved. Should end with /
    such as S:/media/Music Videos - Alternative/
    '''
    # Create a rotating file handler
    handler = RotatingFileHandler(
        f'pmvd-{channel}.log', maxBytes=40000, backupCount=5, encoding='utf-8', errors='replace'
    )
    handler.setLevel(logging.DEBUG)

    # Create a formatter and set it for the handler
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)

    # Add the handler to the logger
    logger.addHandler(handler)

    handler.doRollover()

    # FORMAT = '%(asctime)s %(message)s'
    # logging.basicConfig(filename='python_music_video_downloader.log', level=logging.INFO, format=FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
    # logging.basicConfig(format=FORMAT)
    logger.info('Started')


    msg = f"Subject: {channel} Music Video Download Report\n\n"

    json_songs_filename = f'{channel}-songs.json'
    if not os.path.exists(json_songs_filename):
        try:
            logger.info(f"{json_songs_filename} did not exist. Copying default songs-orig.json instead")
            shutil.copy('songs-orig.json', json_songs_filename)
        except Exception as e:
            logger.error("Error opening the json song files {e}")
            logger.error("songs-orig.json")
            logger.error(json_songs_filename)
            msg += f"Error opening the json song files {e}"
            exit(1)
    
    URL=f"https://xmplaylist.com/station/{channel}"


    # Retrieve the current database of saved songs
    try:
        with open(json_songs_filename) as json_songs_file:
            json_songs = json.load(json_songs_file)
    except Exception as e:
        logger.error("Could not open json file: " + json_songs_filename)
        logger.error("Aborting")
        msg += f"Could not open json file: {json_songs_filename}\n"
        sendNotificationEmail(logger, msg)
        exit(1)


    try:
        # Check the xmplaylist.com for recently played songs
        page = requests.get(URL)
        # print(str(page.status_code))
        soup = BeautifulSoup(page.content, "html.parser")
        # print(str(soup.contents))
        logger.info("Retrieved data from " + URL)
    except:
        logger.error("Could not open " + URL)
        logger.error("Aborting")
        msg += "Could not open " + URL
        sendNotificationEmail(logger, msg)
        exit(1)

    # Get the song information
    song_elements = soup.find_all("div", class_=song_elements_class) # line 157 in sample.html
    # print(song_elements)
    if song_elements is None:
        logger.error("song_elements was None. Aborting")
        exit(1)
        
    msg += "Found " + str(len(song_elements)) + " song elements\r\n"
    logger.info("Found " + str(len(song_elements)) + " song elements")

    # We have the list all of the songs that were recently played, so loop
    # through the list and try to find the music videos for each one.
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
        try:
            songId = songAnchor['href']
        except Exception as e:
            logger.error(f'Could not get a song id. The error was {e}')
            logger.error('songAnchor:')
            logger.error(f'{songAnchor}')
            continue

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
            logger.error("Title element was none. We cannot proccess this song further. Skipping to next song.")
            continue

        # get the list of artists
        artists = song_element.find("ul", class_=artist_class)
        if (artists is not None):
            search_terms += " "
            artistList = ""

            for li in artists.find_all("li"):
                # print("artist: " + li.text, end=" ")
                artistList += li.text.strip() + " "
                # print(artistList)
            logger.info("New song artist(s) = " + artistList)
            msg += artistList + ".\r\n"
        else:
            # print("artists is None")
            msg += "No artists found\r\n"
            logger.error("No artists found. Aborting this song.")
            continue
        
        json_songs['songs'][songId]['song-artist'] = artistList.strip()

        outtmpl:str = ''

        # Now use spotify to get some more metadata
        sp:SpotifyClientCredentials = None
        load_dotenv()
        spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID")
        spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        try:
            logger.debug("Getting the spotify auth manager")
            this_auth_manager = SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret)
            logger.debug("Got the spotify auth manager. Now getting the Spotify instance.")
            sp = spotipy.Spotify(auth_manager=this_auth_manager)
            logger.debug("Got the Spotify instance.")
        except Exception as e:
            logger.info(f"Could not authenticate with Spotify. Will not use it for metadata. {e}")

        if sp is not None:
            release_date = get_song_release_date(sp, songTitle, artistList.strip())
            logger.info(f"Got a release date {release_date}")
            outtmpl = f'{save_dir}%(title)s [{release_date[:4]}].%(ext)s'
        else:
            outtmpl = f'{save_dir}%(title)s [%(release_year)s].%(ext)s'

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
        # logger.info("Getting youtube_id")
        # youtube_id = videosearch.result()["result"][0]["id"]
        json_songs['songs'][songId]['video-url'] = videoUrl
        # print(videoUrl)

        # Options for downloading video and audio
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'cookiefile': 'www.youtube.com_cookies.txt',
            'outtmpl' : outtmpl,
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }

        # Create a yt-dlp object with the given options
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                logger.info(f'Downloading {videoUrl}')
                info_dict = ydl.extract_info(videoUrl, download=True)
                output_filename = ydl.prepare_filename(info_dict)
                json_songs['songs'][songId]['output_filename'] = output_filename
                json_songs['songs'][songId]['video-filename'] = info_dict['requested_downloads'][0]['filepath']
                json_songs['songs'][songId]['resolution'] = info_dict['requested_downloads'][0]['resolution']
                json_songs['songs'][songId]['search-terms'] = search_terms
                json_songs['songs'][songId]['downloaded-date-time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                logger.info(f"* * * * File saved: {info_dict['requested_downloads'][0]['filepath']}")
                msg += f'Searched for {search_terms}\n'
                msg += f'Downloded {info_dict['title']}\n'
                msg += f'Uploaded by {info_dict['uploader']}\n'
                msg += f'{info_dict['requested_downloads'][0]['filepath']}\n'
                msg += f'{videoUrl}\n'
                logger.info(f'Searched for {search_terms}')
                logger.info(f'Downloded {info_dict['title']}')
                logger.info(f'Uploaded by {info_dict['uploader']}')
                logger.info(f'{info_dict['requested_downloads'][0]['filepath']}')
                logger.info(f'{videoUrl}')
            except Exception as e:
                logger.error(f'Error downloading the video: {e}')
                msg += f'Error downloading the video: {e}\n'


    logger.info("Almost done. Writing out the json file now")
    with open(json_songs_filename, 'w') as out_file:
        json.dump(json_songs, out_file, indent=4)
    # print ("All done! Enjoy the videos")

    sendNotificationEmail(logger, msg)
    logger.info('Finished')

# print(args)
# run(channel='altnation', save_dir='S:/media/Music Videos - Alternative/')
# run(channel=args.channel, save_dir=args.save_dir)
# run(channel='1stwave', save_dir='S:/media/Music Videos - First Wave/')
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", help="The last part of the url from xmplaylist.com", default='1stwave')
    parser.add_argument("--save_dir", help="Where do you want to save the music videos", default='S:/media/Music Videos - First Wave/')
    args = parser.parse_args()
    run(channel=args.channel, save_dir=args.save_dir)
