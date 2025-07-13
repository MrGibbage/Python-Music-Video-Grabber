import os
import os.path
# pip install yt-dlp
import yt_dlp
import pprint

def yt_dlp_hook(d):
    print(f'Progress hook, status: {d['status']}')
    if d['status'] == 'finished':
        # pprint.pprint(d)
        file_tuple = os.path.split(os.path.abspath(d['filename']))
        print(f"Done downloading {file_tuple[1]}")


def yt_dlp_download(videoUrl):
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'progress_hooks': [yt_dlp_hook],
        # cookie.txt instructions:
        # Use Chrome
        # Using incognito mode, navigate to youtube.com. Make sure you are logged in.
        # Then navigate to https://www.youtube.com/robots.txt in the same window/tab.
        # Do not close the window/tab.
        # Hit F12, and choose "Application" from the top menu. May need to click the ">>" button to see it.
        # Then navigate to "Cookies" in the left sidebar. (under "Storage")
        # Then use the extension Get Cookies.txt to export the cookies.
        # Save the cookies to a file named "www.youtube.com_cookies.txt" in the same directory as this script.

        'cookiefile': 'www.youtube.com_cookies.txt',
        'verbose' : True,
        'overwrites': True,
        'outtmpl' : 'S:/media/Music Videos - Alternative/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
    }

    # Create a yt-dlp object with the given options
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info_dict = ydl.extract_info(videoUrl, download=True)
            # pprint.pprint(info_dict['requested_downloads'][0])
            output_filename = info_dict['requested_downloads'][0]['filepath']
            pprint.pprint(info_dict)
            print(f"Saved {output_filename}")
        except Exception as e:
            print(f'Error downloading the video: {e}')

if __name__ == "__main__":
    # yt_dlp_download("https://www.youtube.com/watch?v=oEMy1cPYbQ4")
    # yt_dlp_download("https://www.youtube.com/watch?v=pcwlsVBPe-M") # Age-restricted
    yt_dlp_download("https://www.youtube.com/watch?v=P7xnBdx70GU")