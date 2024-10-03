import os
import os.path
import ffmpeg
from pytube import YouTube
import datetime

videoSavePath = "C:\\Github-Projects\\Python Music Video Grabber\\videos\\"

def downloadFromYoutube(yt: YouTube, fname, json_songs, songId, logger):
    video_fname = videoSavePath + "video_" + fname
    audio_fname = videoSavePath + "audio_" + fname
    saved_video = videoSavePath + fname
    

    json_songs['songs'][songId]['video-filename'] = fname
    # print(filename)
    logger.info("Filename: " + fname)

    msg += " url: " + yt.watch_url + "\r\n"

    # save the video (only if it isn't already saved)
    if (not os.path.isfile(videoSavePath + fname)):
        # print("Saving " + videoSavePath + filename)
        logger.info("Saving " + videoSavePath + fname)
        try:
            os.remove("video.mp4")
            os.remove("audio.mp4")
        except:
            pass
        
        sucessfulDownload = False
        try:
            logger.info("Trying 2160p")
            # video = yt.streams.filter(res="1080p", progressive=True, file_extension='mp4').first().download(filename=videoSavePath + fname)
            video = yt.streams.filter(res="2160p", file_extension='mp4', adaptive=True).order_by('resolution').desc().first().download(filename=video_fname)
            audio = yt.streams.get_audio_only().download(filename=audio_fname)
            video_stream = ffmpeg.input(video)
            audio_stream = ffmpeg.input(audio)
            ffmpeg.output(audio_stream, video_stream, saved_video).run()            # os.rename(video,"video.mp4")
            sucessfulDownload = True
            logger.info("Downloaded 2160p video")
            json_songs['songs'][songId]['2160p-video-downloaded'] = fname
        except Exception as err:
            json_songs['songs'][songId]['2160p-video-download-error'] = str(err)
            print(f"There was an error downloading 2160p video {err=}, {type(err)=}")
            logger.info(f"There was an error downloading 2160p video {err=}, {type(err)=}")
            msg += "2160p error\r\n"
            # continue
        
        if (sucessfulDownload == False):
            try:
                logger.info("Trying 1080p")
                # video = yt.streams.filter(res="1080p", progressive=True, file_extension='mp4').first().download(filename=videoSavePath + fname)
                video = yt.streams.filter(res="1080p", file_extension='mp4', adaptive=True).order_by('resolution').desc().first().download(filename=video_fname)
                audio = yt.streams.get_audio_only().download(filename=audio_fname)
                video_stream = ffmpeg.input(video_fname)
                audio_stream = ffmpeg.input(audio_fname)
                ffmpeg.output(audio_stream, video_stream, saved_video).run()            # os.rename(video,"video.mp4")
                sucessfulDownload = True
                logger.info("Downloaded 1080p video")
                json_songs['songs'][songId]['1080p-video-downloaded'] = fname
            except Exception as err:
                json_songs['songs'][songId]['1080p-video-download-error'] = str(err)
                print(f"There was an error downloading 1080p video {err=}, {type(err)=}")
                logger.info(f"There was an error downloading 1080p video {err=}, {type(err)=}")
                msg += "1080p error\r\n"
                # continue
        
        if (sucessfulDownload == False):
            try:
                logger.info("Trying 720p")
                video = yt.streams.filter(res="720p", file_extension='mp4').order_by('resolution').desc().first().download(filename=videoSavePath + fname)
                # os.rename(video,"video.mp4")
                sucessfulDownload = True
                logger.info("Downloaded 720p video")
                json_songs['songs'][songId]['720p-video-downloaded'] = fname
            except Exception as err:
                json_songs['songs'][songId]['720p-video-download-error'] = str(err)
                print(f"There was an error downloading 720p video {err=}, {type(err)=}")
                logger.info(f"There was an error downloading 720p video {err=}, {type(err)=}")
                msg += "720p error\r\n"
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
            except Exception as err:
                json_songs['songs'][songId]['video-download-error'] = str(err)
                print(f"There was an error downloading highest resolution video {err=}, {type(err)=}")
                logger.info(f"There was an error downloading highest resolution video {err=}, {type(err)=}")
                logger.info("Aborting this song.")
                msg += "any resolution error\r\n"
        
        if (sucessfulDownload):
            msg += "Saved new video " + fname + "\r\n"
            logger.info("Saved new video " + fname)
        else:
            msg += "Could not download\r\n"
            logger.info("Could not download")

    else:
        msg += (fname + " already downloaded\r\n")
        json_songs['songs'][songId]['video-already-download'] = videoSavePath + fname
        logger.info(fname + " was already downloaded, but wasn't in the database.")
    #     print (filename + " already downloaded")

    json_songs['songs'][songId]['video-download-full-filename'] = videoSavePath + fname
    json_songs['songs'][songId]['video-download-datetime'] = str(datetime.datetime.now())

    return msg
