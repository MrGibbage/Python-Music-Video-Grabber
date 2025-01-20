import argparse
parser = argparse.ArgumentParser()
parser.add_argument("channel", help="The last part of the url from xmplaylist.com")
parser.add_argument("save_dir", help="where do you want to save the music videos")
args = parser.parse_args()
print(args.channel)

# parser.add_argument("channel", type=str, help='The channel from xmplaylist.com')
# args = parser.parse_args()
# print(args.channel)