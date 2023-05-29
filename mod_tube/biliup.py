import glob
import json
import asyncio
import shutil
from urllib.parse import urlparse
import os
import subprocess

from bilix.sites.bilibili import DownloaderBilibili
from danmakuC.bilibili import proto2ass
import requests

DRY_RUN = True # not upload to IA

DONWLOAD_HOME_DIR =  '~/.tubeup/another_dir'
DELETE_AFTER_UPLOAD = False
COPY_TO_ARCHIVE_HOME_DIR = True # requires DELETE_AFTER_UPLOAD = False
ARCHIVE_HOME_DIR = '~/tube_archive'
ARCHIVE_HOME_DIR = os.path.expanduser(ARCHIVE_HOME_DIR)
DELETE_AFTER_MOVE_TO_ARCHIVE_HOME_DIR = False

if DELETE_AFTER_UPLOAD and COPY_TO_ARCHIVE_HOME_DIR:
    raise Exception("DELETE_AFTER_UPLOAD and COPY_TO_ARCHIVE_HOME_DIR cannot be both True")


def patch():
    from internetarchive.item import Item
    IA_old_upload = Item.upload
    def IA_new_upload(*args, **kwargs):
        kwargs['delete'] = DELETE_AFTER_UPLOAD
        print(args, kwargs)
        if not DRY_RUN:
            IA_old_upload(*args, **kwargs)
        else:
            print("== Dry run ==")
    Item.upload = IA_new_upload

    #
    from tubeup.__main__ import TubeUp
    TubeUp_old_upload_ia = TubeUp.upload_ia
    def TubeUp_new_upload_ia(self, videobasename, custom_meta=None):
        # print("Hello from new_upload_ia")
        hooker_before_upload_ia(self, videobasename, custom_meta)
        identifier, metadata = TubeUp_old_upload_ia(self, videobasename, custom_meta)
        hooker_after_upload_ia(self, identifier, metadata, videobasename)

        return identifier, metadata
    TubeUp.upload_ia = TubeUp_new_upload_ia

    #
    TubeUp_old__init__ = TubeUp.__init__
    def new__init__(self, *args, **kwargs):
        kwargs['dir_path'] = DONWLOAD_HOME_DIR
        TubeUp_old__init__(self, *args, **kwargs)
    TubeUp.__init__ = new__init__

def hooker_before_upload_ia(self, videobasename, custom_meta):
    info_json_cleaner(videobasename)
    download_bilibili_video_detail(videobasename)
    # delete_file(videobasename + '.info.json')
    down_pb(videobasename)
    gen_ass_from_pb(videobasename)

def down_pb(videobasename):
    basename = os.path.basename(videobasename)
    AVorBV: str = basename.split('_')[0]
    danmaku_dir_tmp = os.path.join(videobasename, 'danmaku_tmp/')
    os.makedirs(danmaku_dir_tmp, exist_ok=True)
    print('danma',AVorBV)
    url = f'https://www.bilibili.com/video/{AVorBV}/'
    d = DownloaderBilibili(hierarchy=False)
    d.progress.start()
    async def do():
        cor = d.get_dm(url=url, path=danmaku_dir_tmp)
        await asyncio.gather(cor)
        await d.aclose()
    asyncio.run(do())

    # find the firsy pb file in danmaku_dir_tmp
    danmaku_files = os.listdir(danmaku_dir_tmp)
    pb_file = None
    for pb_file in danmaku_files:
        pb_file = pb_file if pb_file.endswith('.pb') else None
        if pb_file is not None:
            break
    shutil.move(
        os.path.join(danmaku_dir_tmp, pb_file), videobasename + '.danmaku.pb'
    )
    os.removedirs(danmaku_dir_tmp)


def gen_ass_from_pb(vieobasename):
    danmaku_pb = vieobasename + '.danmaku.pb'
    ass_file = vieobasename + '.danmaku.ass'
    if not os.path.exists(danmaku_pb):
        return
    with open(danmaku_pb, 'rb') as f:
        ass_text = proto2ass(f.read(), 1920, 1080)    
    with open(ass_file, 'w', encoding='utf-8') as f:
        f.write(ass_text)
    print('== danmaku ass')

def delete_all_same_key_nodes(json_data, key: str):
    """
    Delete all nodes with the same key in the JSON data.
    """
    for k in list(json_data.keys()):
        if k.lower() == key.lower():
            del json_data[k]
        else:
            if isinstance(json_data[k], dict):
                delete_all_same_key_nodes(json_data[k], key)
            elif isinstance(json_data[k], list):
                for i in json_data[k]:
                    if isinstance(i, dict):
                        delete_all_same_key_nodes(i, key)

def info_json_cleaner(videobasename):
    json_metadata_filepath = videobasename + '.info.json'
    with open(json_metadata_filepath, 'r', encoding='utf-8') as f:
        vid_meta = json.load(f)
    
    for format in vid_meta.get('formats', {}):
        Url = urlparse(format['url']) if 'url' in format else None
        if 'url' in format:
            del format['url']

    delete_all_same_key_nodes(vid_meta, 'cookie')
    delete_all_same_key_nodes(vid_meta, 'http_headers')

    with open(videobasename + '.yt-dlp.info.json', 'w', encoding='utf-8') as f:
        f.write(json.dumps(vid_meta, indent=4, ensure_ascii=False))

    # move abcd/efg.info.json to abcd/_efg.info.json
    # to hide it from tubeup
    shutil.move(json_metadata_filepath, 
        os.path.join(os.path.dirname(json_metadata_filepath), '_' + os.path.basename(json_metadata_filepath)))
    # move abcd/efg.yt-dlp.info.json to abcd/efg.info.json
    # to make it public
    shutil.move(videobasename + '.yt-dlp.info.json', videobasename + '.info.json')


def hooker_after_upload_ia(self, identifier, metadata, videobasename):
    if COPY_TO_ARCHIVE_HOME_DIR:
        copy_basename_to_archive_home_dir(identifier, videobasename)
    if DELETE_AFTER_MOVE_TO_ARCHIVE_HOME_DIR:
        delete_basename_in_download_dir(videobasename)

def download_bilibili_video_detail(videobasename):
    url = 'https://api.bilibili.com/x/web-interface/view/detail'
    basename = os.path.basename(videobasename)
    AVorBV: str = basename.split('_')[0]
    print(AVorBV)
    if AVorBV.startswith('BV'):
        print('== BV:', AVorBV)
        r = requests.get(url, params={'bvid': AVorBV})
    elif AVorBV.startswith('AV'):
        print('== AV:', AVorBV)
        r = requests.get(url, params={'aid': AVorBV})
    print('.')
    r.raise_for_status()
    
    with open(videobasename + '.bili.info.json', 'w', encoding='utf-8') as f:
        # f.write(json.dumps(r.json(), indent=4, ensure_ascii=False))
        f.write(r.text)

def copy_basename_to_archive_home_dir(identifier, videobasename):
    files = glob.glob(videobasename + '*')
    print('Copying...')
    for file in files:
        dest = os.path.join(ARCHIVE_HOME_DIR, identifier, os.path.basename(file))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        cp_reflink(file, dest)

def delete_basename_in_download_dir(videobasename):
    files = glob.glob(videobasename + '*')
    for file in files:
        delete_file(file)

def cp_reflink(src, dst):
    subprocess.run(['cp', '--reflink=auto', src, dst])

def delete_file(path):
    os.remove(path)

if __name__ == '__main__':
    print('MOD...')
    patch()
    from tubeup.__main__ import main as tube_main
    tube_main()