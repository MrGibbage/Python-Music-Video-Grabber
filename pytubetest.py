from pytube import YouTube
from pytube.innertube import _default_clients

_default_clients["ANDROID"]["context"]["client"]["clientVersion"] = "19.08.35"
_default_clients["ANDROID_MUSIC"] = _default_clients["ANDROID"]

# works
# yt1 = YouTube('https://www.youtube.com/watch?v=B3eAMGXFw1o', use_oauth=True, allow_oauth_cache=True)
# yt1.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first().download()

# does not work
# yt2 = YouTube('https://www.youtube.com/watch?v=zE3WuMPaopo', use_oauth=True, allow_oauth_cache=True) 
# yt2.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first().download()


# yt2 = YouTube('https://www.youtube.com/watch?v=oEiPkSxQVk8', use_oauth=True, allow_oauth_cache=True) 
# yt2.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first().download()


yt2 = YouTube('https://www.youtube.com/watch?v=GXiAyxVQgb4', use_oauth=True, allow_oauth_cache=True) 
yt2.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first().download()
