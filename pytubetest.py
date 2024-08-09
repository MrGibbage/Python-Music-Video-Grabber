from pytube import YouTube
from pytube.innertube import _default_clients

_default_clients["ANDROID"]["context"]["client"]["clientVersion"] = "19.08.35"
_default_clients["ANDROID_MUSIC"] = _default_clients["ANDROID"]

# put urls here that failed in the past. Then try to run it directly using the code below.
# https://www.youtube.com/watch?v=GXiAyxVQgb4
# https://www.youtube.com/watch?v=1rAQGxRFvN0

# These never seem to work.
# https://www.youtube.com/watch?v=NtEM0dYhEaA
# https://www.youtube.com/watch?v=1rAQGxRFvN0
# https://www.youtube.com/watch?v=Y8EPs0D7siE


yt2 = YouTube('https://www.youtube.com/watch?v=Mx2xsH7a9cc', use_oauth=True, allow_oauth_cache=True) 
yt2.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc().first().download()
