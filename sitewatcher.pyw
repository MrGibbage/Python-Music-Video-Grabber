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
import os, sys
import smtplib
import shutil
from datetime import datetime

# pip install bs4
# from bs4 import BeautifulSoup
# pip install python-dotenv
from dotenv import load_dotenv
# pip install pytube (see comments below)
# from pytube.innertube import _default_clients
# pip install requests
import requests
from requests.models import Response
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

cwd: str = os.getcwd()
if getattr(sys, 'frozen', False):
    dir_path = os.path.dirname(sys.executable)
elif __file__:
    dir_path = os.path.dirname(__file__)

print(f'{cwd=}')
print(f'{dir_path=}')

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
    msg += 'Sending notification email now\r\n'
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
        msg += f"Error sending email {err=}, {type(err)=}\r\n"


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
    try:
        handler = RotatingFileHandler(
            dir_path + f'\\pmvd-{channel}.log', maxBytes=40000, backupCount=5, encoding='utf-8', errors='replace'
        )
    except Exception as e:
        # print(f"Error opening the log files from {dir_path + f'\\pmvd-{channel}.log'}\nThe error was {e}")
        logger.info(f"Error opening the log files from {dir_path + f'\\pmvd-{channel}.log'}\nThe error was {e}")
        msg += f"Error opening the log files from {dir_path + f'\\pmvd-{channel}.log'}\nThe error was {e}\r\n"
        exit(1)

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

    # Done with setting up logging. Now get the database of songs already downloaded
    json_songs_filename = dir_path + f'\\{channel}-songs.json'
    if not os.path.exists(json_songs_filename):
        try:
            logger.info(f"{json_songs_filename} did not exist. Copying default songs-orig.json instead")
            msg += f"{json_songs_filename} did not exist. Copying default songs-orig.json instead\r\n"
            shutil.copy('songs-orig.json', json_songs_filename)
        except Exception as e:
            logger.error("Error opening the json song files {e}")
            msg += "Error opening the json song files {e}\r\n"
            logger.error("songs-orig.json")
            logger.error(json_songs_filename)
            exit(1)
    
    try:
        with open(json_songs_filename) as json_songs_file:
            json_songs = json.load(json_songs_file)
    except FileNotFoundError:
        # print(f"Error: The file '{json_songs_filename}' was not found.")
        logger.error("json file was not found: " + json_songs_filename)
        logger.error("Aborting")
        msg += f"json file was not found: {json_songs_filename}\n"
        exit(1)
    except json.JSONDecodeError:
        # print(f"Error: The file '{json_songs_filename}' is not a valid JSON.")    
        logger.error(f"json file {json_songs_filename} is not a valid JSON file")
        logger.error("Aborting")
        msg += f"json file: {json_songs_filename} is not a valid JSON file\n"
        exit(1)
    except Exception as e:
        # print(type(e))
        # print(f'There was an unexpected error: {e}')
        logger.error("Could not open json file: " + json_songs_filename)
        logger.error(f'The error was {e}')
        logger.error("Aborting")
        msg += f"Could not open json file: {json_songs_filename}\n"
        sendNotificationEmail(logger, msg)
        exit(1)

    # Get the API version
    # This code was last updated with 
    # openapi version = 3.1.0
    # info['version'] = 2.0.0
    APIURL=f"https://xmplaylist.com/api/spec"
    try:
        api_info: Response = requests.get(APIURL)
        openapi_ver = api_info.json()['openapi']
        info_ver = api_info.json()['info']['version']
        logger.info(f"Retrieved API data from {APIURL}. openapi_ver = {openapi_ver} (expected 3.1.0). info_ver = {info_ver} (expected 2.0.0)")
        msg += f"Retrieved API data from {APIURL}. openapi_ver = {openapi_ver} (expected 3.1.0). info_ver = {info_ver} (expected 2.0.0\r\n"
    except:
        logger.error("Could not open " + APIURL)
        logger.error("Aborting")
        msg += "Could not open " + APIURL + "\r\n"
        sendNotificationEmail(logger, msg)
        exit(1)


    # Get the most recent songs played
    URL=f"https://xmplaylist.com/api/station/{channel}/newest"
    try:
        recentSongs: Response = requests.get(URL)
        logger.info("Retrieved data from " + URL)
        msg += "Retrieved data from " + URL + "\r\n"
        # print(recentSongs.json())
    except:
        logger.error("Could not open " + URL)
        logger.error("Aborting")
        msg += "Could not open " + URL + "\r\n"
        sendNotificationEmail(logger, msg)
        exit(1)
    
    # print(recentSongs.json())

    # We have the list all of the songs that were recently played, so loop
    # through the list and try to find the music videos for each one.
    for idx, song_element in enumerate(recentSongs.json()['results'], 1):
        # print(f'{idx=}, {song_element=}')
        logger.info("Song number " + str(idx))
        msg += "Song number " + str(idx) + ": "
        print(f"Song number {str(idx)}")
        search_terms = ""
        songTitle = ""

        try:
            # print(f'{song_element=}')
            songId = song_element['track']['id']
            # songId = song_element['results']
            logger.info(f'Got a songId: {songId}')
            msg += f'Got a songId: {songId}\r\n'
        except Exception as e:
            logger.error(f'Could not get a song id. Skipping this song. The error was {e}')
            msg += f'Could not get a song id. Skipping this song. The error was {e}\r\n'
            continue

        # Check to see if we have already downloaded this song
        if songId in json_songs['songs'].keys():
            logger.info(songId + " is already added to database")
            existingSongTitle = json_songs['songs'][songId]['song-title']
            existingSongArtist = json_songs['songs'][songId]['song-artist']
            # existingFileName = json_songs['songs'][songId]['video-filename']
            logger.info("Existing song title: " + json_songs['songs'][songId]['song-title'])
            logger.info("Existing song artist: " + json_songs['songs'][songId]['song-artist'])
            logger.info("Existing song file name: " + json_songs['songs'][songId]['video-filename'])
            # print(json_songs['songs'][songId]['song-title'])
            msg += existingSongTitle + " by " + existingSongArtist + " already in the database\r\n"
            print(f'{existingSongTitle} by {existingSongArtist} already in the database. Skipping to next song.')
            logger.info("Nothing to do. Skipping to next song.")
            continue

        # if we are here, then it must be a new song
        # print("Looks like this is a new song!")
        json_songs['songs'][songId] = {}

        # Get the song title
        try:
            songTitle = song_element['track']['title']
        except Exception as e:
            logger.error(f'There was an error getting the songTitle. Skipping this song. The error was {e}')
            msg += f'Could not get a song Title. Skipping this song. The error was {e}\r\n'
            continue

        if (songTitle is not None):
            #   print("title: " + title_element.text)
            logger.info("New song title: " + songTitle)
            print(f"New song title: {songTitle}")
            msg += "**NEW** " + songTitle + " by "
            search_terms += songTitle
            json_songs['songs'][songId]['song-title'] = songTitle
        else:
            # print("title_element is None")
            msg += "Song Title was none\r\n"
            logger.error("Song Title was none. We cannot proccess this song further. Skipping to next song.")
            continue

        # get the list of artists
        artists = song_element['track']['artists']
        print(f'{artists=}')
        if (artists is not None):
            search_terms += " "
            artistList = " ".join(artists)

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
        # https://developer.spotify.com/dashboard
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
            msg += "Got the Spotify instance.\r\n"
        except Exception as e:
            logger.info(f"Could not authenticate with Spotify. Will not use it for metadata. {e}")
            msg += f"Could not authenticate with Spotify. Will not use it for metadata. {e}"

        if sp is not None:
            release_date = get_song_release_date(sp, songTitle, artistList.strip())
            logger.info(f"Got a release date {release_date}")
            msg += f"Got a release date {release_date}\r\n"
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
        try:
            videosearch = VideosSearch(search_terms, limit=1) # requires httpx == 0.27.2
            videoUrl = videosearch.result()["result"][0]["link"] 
            logger.info(videoUrl)
            msg += videoUrl + "\r\n"
        except Exception as e:
            logger.error(f'There was an error downloading the video. The error was {e}. Skipping this song.')
            msg += f'There was an error downloading the video. The error was {e}. Skipping this song.'
            continue

        json_songs['songs'][songId]['video-url'] = videoUrl
        # print(videoUrl)

        # Options for downloading video and audio
        cookie_file = 'www.youtube.com_cookies.txt'
        if not os.path.exists('www.youtube.com_cookies.txt'):
            logger.warning(f'Could not find cookie file {cookie_file} in {dir_path}')
            msg += f'Could not find cookie file {cookie_file} in {dir_path}\n'

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'cookiefile': cookie_file,
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
                msg += f'Downloading {videoUrl}\n'
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
                print(f'Downloded {info_dict['title']}')
                msg += f'Uploaded by {info_dict['uploader']}\n'
                msg += f'{info_dict['requested_downloads'][0]['filepath']}\n'
                logger.info(f'Searched for {search_terms}')
                logger.info(f'Downloded {info_dict['title']}')
                logger.info(f'Uploaded by {info_dict['uploader']}')
                logger.info(f'{info_dict['requested_downloads'][0]['filepath']}')
            except Exception as e:
                logger.error(f'Error downloading the video: {e}')
                msg += f'ERROR ERROR ERROR downloading the video: {e}\n'
                print(f'ERROR ERROR ERROR downloading the video: {e}')
                json_songs['songs'][songId]['error-message'] = e
                continue


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
    # parser.add_argument("--channel", help="The last part of the url from xmplaylist.com", default='1stwave')
    # parser.add_argument("--save_dir", help="Where do you want to save the music videos", default='S:/media/Music Videos - First Wave/')
    parser.add_argument("--channel", help="The last part of the url from xmplaylist.com", default='altnation')
    parser.add_argument("--save_dir", help="Where do you want to save the music videos", default='S:/media/Music Videos - Alternative/')
    args = parser.parse_args()
    print(f'{args.channel=}')
    print(f'{args.save_dir=}')
    run(channel=args.channel, save_dir=args.save_dir)
