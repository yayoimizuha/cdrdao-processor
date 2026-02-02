from os.path import dirname, join, pathsep
from os import environ

environ["PATH"] = join(dirname(__file__), "discid_dll") + pathsep + environ["PATH"]

import discid

print(device := discid.get_default_device())
print(info := discid.read(device, features=["mcn", "isrc"]))
print(dir(info))
print(info.freedb_id)
print(info.mcn)
print(info.tracks[0].isrc)
