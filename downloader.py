import os
import os.path
import ffmpeg
from pytube import YouTube
from pytube.innertube import _default_clients
import datetime
from slugify import slugify
from asyncio import subprocess
import subprocess

videoSavePath = "C:\\Github-Projects\\Python Music Video Grabber\\videos\\"

def easyDownloadFromYoutube(videoUrl, artist, songTitle):
    _default_clients["ANDROID"]["context"]["client"]["clientVersion"] = "19.08.35"
    _default_clients["ANDROID_MUSIC"] = _default_clients["ANDROID"]
    # print(videoUrl)
    yt = YouTube(videoUrl, use_oauth=True, allow_oauth_cache=True)
    # print(yt.streams)

    videoYear = yt.publish_date.year
    fname = slugify(artist, lowercase=False, separator=" ") + " - " + \
        slugify(songTitle, lowercase=False, separator=" ") + \
        " (" + str(videoYear) + ").mp4"
    saved_video = videoSavePath + fname
    localMsg = "Looking for " + videoUrl + "\r\n"

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

    sucessfulDownload = False
    try:
        # video = yt.streams.filter(res="1080p", progressive=True, file_extension='mp4').first().download(filename=videoSavePath + fname)
        videoFirstStream = yt.streams.filter(file_extension='mp4', progressive=False, res="2160p").first()
        videoFirstStream.download(filename="vid.mp4")
        yt.streams.get_audio_only().download(filename="aud.mp4")

        subprocess.run([
                        'ffmpeg',
                        '-i', 'vid.mp4',
                        '-i', 'aud.mp4',
                        '-c:v', 'copy',
                        '-c:a', 'aac',
                        '-loglevel', 'info',
                        saved_video
                        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)

        # ffmpeg.output(audio_stream, video_stream, "out.mp4").run()            # os.rename(video,"video.mp4")
        sucessfulDownload = True
        localMsg += "Saved 2160p\r\n"
        localMsg += saved_video  + "\r\n"
    except Exception as err:
        # print(f"There was an error downloading 2160p video {err=}, {type(err)=}")
        localMsg += "2160p error\r\n"
    
    if (sucessfulDownload == False):
        try:
            # video = yt.streams.filter(res="1080p", progressive=True, file_extension='mp4').first().download(filename=videoSavePath + fname)
            videoFirstStream = yt.streams.filter(file_extension='mp4', progressive=False, res="1080p").first()
            videoFirstStream.download(filename="vid.mp4")
            yt.streams.get_audio_only().download(filename="aud.mp4")

            subprocess.run([
                            'ffmpeg',
                            '-i', 'vid.mp4',
                            '-i', 'aud.mp4',
                            '-c:v', 'copy',
                            '-c:a', 'aac',
                            '-loglevel', 'info',
                            saved_video
                            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)
            sucessfulDownload = True
            localMsg += "Saved 1080p\r\n"
            localMsg += saved_video + "\r\n"
        except Exception as err:
            localMsg += "1080p error\r\n"
    
    if (sucessfulDownload == False):
        try:
            video = yt.streams.filter(res="720p", file_extension='mp4').order_by('resolution').desc().first().download(filename=videoSavePath + fname)
            # os.rename(video,"video.mp4")
            sucessfulDownload = True
            localMsg += "Saved 720p\r\n"
            localMsg += saved_video + "\r\n"
        except Exception as err:
            localMsg += "720p error\r\n"

    if (sucessfulDownload == False):
        try:
            video = yt.streams.filter(file_extension='mp4').order_by('resolution').desc().first().download(filename=videoSavePath + fname)
            # os.rename(video,"video.mp4")
            sucessfulDownload = True
            localMsg += "Saved " + yt.streams.get_highest_resolution().resolution + "\r\n"
            localMsg += saved_video + "\r\n"
        except Exception as err:
            # print(f"There was an error downloading highest resolution video {err=}, {type(err)=}")
            localMsg += "any resolution error\r\n"
    
    if (sucessfulDownload):
        localMsg += "Saved new video " + fname + "\r\n"
    else:
        localMsg += "Could not download\r\n"
    print(localMsg)

def downloadFromYoutube(videoUrl, json_songs, songId, logger, artistList, songTitle):
    _default_clients["ANDROID"]["context"]["client"]["clientVersion"] = "19.08.35"
    _default_clients["ANDROID_MUSIC"] = _default_clients["ANDROID"]
    # print(videoUrl)
    yt = YouTube(videoUrl, use_oauth=True, allow_oauth_cache=True)
    # print(yt.streams)

    videoYear = yt.publish_date.year
    fname = slugify(artistList, lowercase=False, separator=" ") + " - " + \
        slugify(songTitle, lowercase=False, separator=" ") + \
        " (" + str(videoYear) + ").mp4"
    
    video_fname = videoSavePath + "video_" + fname
    audio_fname = videoSavePath + "audio_" + fname
    saved_video = videoSavePath + fname
    

    json_songs['songs'][songId]['video-filename'] = fname
    # print(filename)
    logger.info("Filename: " + fname)

    localMsg = ""
    localMsg += " url: " + yt.watch_url + "\r\n"

    # save the video (only if it isn't already saved)
    if (not os.path.isfile(videoSavePath + fname)):
        # print("Saving " + videoSavePath + filename)
        logger.info("Saving " + videoSavePath + fname)
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
        
        sucessfulDownload = False
        try:
            logger.info("Trying 2160p")
            # video = yt.streams.filter(res="1080p", progressive=True, file_extension='mp4').first().download(filename=videoSavePath + fname)
            videoFirstStream = yt.streams.filter(file_extension='mp4', progressive=False, res="2160p").first()
            videoFirstStream.download(filename="vid.mp4")
            yt.streams.get_audio_only().download(filename="aud.mp4")

            subprocess.run([
                            'ffmpeg',
                            '-i', 'vid.mp4',
                            '-i', 'aud.mp4',
                            '-c:v', 'copy',
                            '-c:a', 'aac',
                            '-loglevel', 'info',
                            saved_video
                            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)

            # ffmpeg.output(audio_stream, video_stream, "out.mp4").run()            # os.rename(video,"video.mp4")
            sucessfulDownload = True
            logger.info("Downloaded 2160p video")
            json_songs['songs'][songId]['2160p-video-downloaded'] = fname
            localMsg += "Saved 2160p\r\n"
        except Exception as err:
            json_songs['songs'][songId]['2160p-video-download-error'] = str(err)
            # print(f"There was an error downloading 2160p video {err=}, {type(err)=}")
            logger.info(f"There was an error downloading 2160p video {err=}, {type(err)=}")
            # continue
        
        if (sucessfulDownload == False):
            try:
                logger.info("Trying 1080p")
                # video = yt.streams.filter(res="1080p", progressive=True, file_extension='mp4').first().download(filename=videoSavePath + fname)
                videoFirstStream = yt.streams.filter(file_extension='mp4', progressive=False, res="1080p").first()
                videoFirstStream.download(filename="vid.mp4")
                yt.streams.get_audio_only().download(filename="aud.mp4")

                subprocess.run([
                                'ffmpeg',
                                '-i', 'vid.mp4',
                                '-i', 'aud.mp4',
                                '-c:v', 'copy',
                                '-c:a', 'aac',
                                '-loglevel', 'info',
                                saved_video
                                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS)
                sucessfulDownload = True
                logger.info("Downloaded 1080p video")
                json_songs['songs'][songId]['1080p-video-downloaded'] = fname
                localMsg += "Saved 1080p\r\n"
            except Exception as err:
                json_songs['songs'][songId]['1080p-video-download-error'] = str(err)
                # print(f"There was an error downloading 1080p video {err=}, {type(err)=}")
                logger.info(f"There was an error downloading 1080p video {err=}, {type(err)=}")
                # continue
        
        if (sucessfulDownload == False):
            try:
                logger.info("Trying 720p")
                video = yt.streams.filter(res="720p", file_extension='mp4').order_by('resolution').desc().first().download(filename=videoSavePath + fname)
                # os.rename(video,"video.mp4")
                sucessfulDownload = True
                logger.info("Downloaded 720p video")
                json_songs['songs'][songId]['720p-video-downloaded'] = fname
                localMsg += "Saved 720p\r\n"
            except Exception as err:
                json_songs['songs'][songId]['720p-video-download-error'] = str(err)
                # print(f"There was an error downloading 720p video {err=}, {type(err)=}")
                logger.info(f"There was an error downloading 720p video {err=}, {type(err)=}")
                # continue

        if (sucessfulDownload == False):
            try:
                logger.info("Trying for best resolution")
                video = yt.streams.filter(file_extension='mp4').order_by('resolution').desc().first().download(filename=videoSavePath + fname)
                logger.info("Best resolution: " + yt.streams.get_highest_resolution().resolution)
                # os.rename(video,"video.mp4")
                sucessfulDownload = True
                logger.info("Downloaded best resolution: " + yt.streams.get_highest_resolution().resolution)
                json_songs['songs'][songId]['video-downloaded'] = fname
                json_songs['songs'][songId]['video-downloaded-resolution'] = yt.streams.get_highest_resolution().resolution
                localMsg += "Saved " + yt.streams.get_highest_resolution().resolution
            except Exception as err:
                json_songs['songs'][songId]['video-download-error'] = str(err)
                # print(f"There was an error downloading highest resolution video {err=}, {type(err)=}")
                logger.info(f"There was an error downloading highest resolution video {err=}, {type(err)=}")
                logger.info("Aborting this song.")
                localMsg += "any resolution error\r\n"
        
        if (sucessfulDownload):
            localMsg += "Saved new video " + fname + "\r\n"
            logger.info("Saved new video " + fname)
        else:
            localMsg += "Could not download\r\n"
            logger.info("Could not download")

    else:
        localMsg += (fname + " already downloaded\r\n")
        json_songs['songs'][songId]['video-already-download'] = videoSavePath + fname
        logger.info(fname + " was already downloaded, but wasn't in the database.")
    #     print (filename + " already downloaded")

    json_songs['songs'][songId]['video-download-full-filename'] = videoSavePath + fname
    json_songs['songs'][songId]['video-download-datetime'] = str(datetime.datetime.now())

    return localMsg

easyDownloadFromYoutube("https://www.youtube.com/watch?v=oEMy1cPYbQ4", "The Dare", "You're Invited")