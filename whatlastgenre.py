#!/usr/bin/env python

from __future__ import division
from ConfigParser import SafeConfigParser
from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import namedtuple
from difflib import get_close_matches, SequenceMatcher
from glob import glob
from inspect import stack
import json
import musicbrainzngs
import mutagen
import operator
import os
import re
import requests
import string
import sys

# Constant Score Multipliers
SM_WHATCD = 1.5
SM_LASTFM = 0.75
SM_MBRAIN = 1.0
SM_DISCOG = 1.0

TAlbumInfo = namedtuple('AlbumInfo', 'artist, album, va')
def AlbumInfo(artist, album, va=False):
    return TAlbumInfo(artist, album, va)

class GenreTags:
    def __init__(self):
        self.tags = {}
        # add some basic genre tags (from id3)
        tags = ['Acapella', 'Acid', 'Acid Jazz', 'Acid Punk', 'Acoustic', 'Alternative',
                'Alternative Rock', 'Ambient', 'Anime', 'Avantgarde', 'Ballad', 'Bass', 'Beat',
                'Bebob', 'Big Band', 'Black Metal', 'Bluegrass', 'Blues', 'Booty Bass', 'BritPop',
                'Cabaret', 'Celtic', 'Chamber Music', 'Chanson', 'Chorus', 'Christian',
                'Classic Rock', 'Classical', 'Club', 'Comedy', 'Country', 'Crossover', 'Cult',
                'Dance', 'Dance Hall', 'Darkwave', 'Death Metal', 'Disco', 'Dream', 'Drum & Bass',
                'Easy Listening', 'Electronic', 'Ethnic', 'Euro-House', 'Euro-Techno', 'Euro-Dance',
                'Fast Fusion', 'Folk', 'Folk-Rock', 'Freestyle', 'Funk', 'Fusion', 'Gangsta', 'Goa',
                'Gospel', 'Gothic', 'Gothic Rock', 'Grunge', 'Hard Rock', 'Hardcore', 'Heavy Metal',
                'Hip-Hop', 'House', 'Indie', 'Industrial', 'Instrumental', 'Jazz', 'Jazz+Funk',
                'Jungle', 'Latin', 'Lo-Fi', 'Meditative', 'Metal', 'Musical', 'New Age', 'New Wave',
                'Noise', 'Oldies', 'Opera', 'Other', 'Pop', 'Progressive Rock', 'Psychedelic',
                'Psychedelic Rock', 'Punk', 'Punk Rock', 'R&B', 'Rap', 'Rave', 'Reggae', 'Retro',
                'Revival', 'Rhythmic Soul', 'Rock', 'Rock & Roll', 'Salsa', 'Samba', 'Ska', 'Slow Jam',
                'Slow Rock', 'Sonata', 'Soul', 'Soundtrack', 'Southern Rock', 'Space', 'Speech',
                'Swing', 'Symphonic Rock', 'Symphony', 'Synthpop', 'Tango', 'Techno', 'Thrash Metal',
                'Trance', 'Tribal', 'Trip-Hop', 'Vocal']
        for tag in tags:
            self.add(tag, 0.05)
        # TODO: add more
        tags = ['Chillout', 'Downtempo', 'Electro-Swing', 'Female Vocalist', 'Future Jazz', 'German',
                'German Hip-Hop', 'Jazz-Hop', 'Tech-House']
        for tag in tags:
            self.add(tag, 0.1)
        for tag in conf.genre_score_up:
            self.add(tag, 0.2)
        for tag in conf.genre_score_down:
            self.add(tag, -0.2)

    def __replace_tag(self, tag):
        rplc = {'deutsch': 'german', 'frensh': 'france', 'hip hop': 'hip-hop', 'hiphop': 'hip-hop',
                'prog ': 'progressive', 'rnb': 'r&b', 'trip hop': 'trip-hop', 'triphop': 'trip-hop'}
        for (a, b) in rplc.items():
            if string.find(tag.lower(), a) is not -1:
                return string.replace(tag.lower(), a, b)
        return tag;
    
    def __format_tag(self, tag):
        if tag.upper() in conf.genre_uppercase:
            return string.upper(tag)
        return tag.title()

    def add(self, name, score):
        if len(name) not in range(2, 20) \
            or re.match('([0-9]{2}){1,2}s?', name) is not None \
            or (score < 0.025 and stack()[1][3] is not '__init__'):
            return
        name = self.__replace_tag(name)
        name = self.__format_tag(name)
        #if args.verbose and stack()[1][3] is not '__init__':
        #    print "  %s (%.3f)" % (name, score)
        found = get_close_matches(name, self.tags.keys(), 1, 0.858) # don't change this, add replaces instead
        if found:
            self.tags[found[0]] = (self.tags[found[0]] + score) * 1; # FIXME: find good value for modifier
            if args.verbose and SequenceMatcher(None, name, found[0]).ratio() < 0.99:
                print "  %s is the same tag as %s (%.3f)" % (name, found[0], self.tags[found[0]]) 
        else:
            self.tags.update({name: score})

    def get(self, limit):
        # only good ones
        tags = dict((name, score) for name, score in self.tags.iteritems() if score > 0.4)
        if args.verbose:
            print "Good tags: ",
            for key, value in sorted(tags.iteritems(), key=lambda (k, v): (v, k), reverse=True):
                print "%s: %.2f, " % (key, value),
            print
        # get sorted list from it
        tags = sorted(tags, key=self.tags.get, reverse=True)
        # filter them
        return self.filter_taglist(tags)[:limit];

    def filter_taglist(self, tags):
        # apply whitelist
        if conf.genre_whitelist:
            wl = conf.genre_whitelist
            tags = (tag for tag in tags if tag in wl)
        # or apply blacklist
        elif conf.genre_blacklist:
            bl = conf.genre_blacklist
            tags = (tag for tag in tags if tag not in bl)
        return list(tags)


def scan_folder(path):
    try:
        for thefile in os.listdir(path):
            thefile = os.path.join(path, thefile)
            if os.path.isdir(thefile):
                scan_folder(thefile)
            elif os.path.splitext(thefile)[1].lower() in [".flac", ".ogg", ".mp3"]:
                handle_album(path, os.path.splitext(thefile)[1]);
                print
                break;
    except OSError, e:
        print e


def handle_album(path, filetype):
    
    tracks = glob(os.path.join(path, '*' + filetype)) 
    
    print "%s-album in %s (%d tracks)..." % (filetype[1:], path, len(tracks))
    
    ai = get_album_info(tracks)
    if not ai:
        print "Warning: Not all tracks have the same (or an) album-tag. Skipping for safety..."
        return
    
    genreTags, releaseType = get_data(ai)
    
    if args.tag_release and releaseType:
        print "Release type: %s" % releaseType
    if genreTags:
        print "Genre tags: %s" % liststr(genreTags)
    
    if args.stats:
        for tag in genreTags:
            if stats.has_key(tag):
                stats[tag] = stats[tag] + 1
            else:
                stats.update({tag: 1})
    
    if args.dry_run:
        print "DRY-Mode! Skipping saving of metadata..."
        return

    print "Saving metadata..."
    for track in tracks:
        set_meta(track, genreTags, releaseType)


def get_album_info(tracks):
    va = False
    try:
        meta = get_meta(tracks[0])
        #if args.verbose: print meta.pprint()
        for track in tracks:
            meta2 = get_meta(track)
            if not meta2 or meta['album'][0] != meta2['album'][0]:
                return False
            if meta['artist'][0] != meta2['artist'][0]:
                va = True
        return AlbumInfo(meta['artist'][0] if not va else "", meta['album'][0], va)
    except Exception, e:
        print e
    return False


def get_meta(track):
    try:
        return mutagen.File(track, easy=True)
    except Exception, e:
        print e
    return False


def set_meta(track, genreTags, releaseType):
    try:
        meta = mutagen.File(track, easy=True)
        if args.tag_release and releaseType and os.path.splitext(track)[1].lower() in ['.flac', '.ogg']:
            meta['release'] = releaseType
        if genreTags:
            meta['genre'] = genreTags
        meta.save()
    except Exception, e:
        print e


def get_data(ai):
    print "Getting data for \"%s - %s\"..." % (('VA' if ai.va else ai.artist), ai.album)
    genretags = GenreTags()
    releaseType = None
    mbrainzids = None
    
    if not args.no_lastfm:
        if args.verbose: print "Last.FM..."
        get_data_lastfm(ai, genretags)
    if not args.no_whatcd:
        if args.verbose: print "What.CD..."
        releaseType = get_data_whatcd(ai, genretags)
    if not args.no_mbrainz:
        if args.verbose: print "MusicBrainz..."
        mbrainzids = get_data_mbrainz(ai, genretags)
    if not args.no_discogs:
        if args.verbose: print "Discogs..."
        get_data_discogs(ai, genretags)

    return genretags.get(args.tag_limit), releaseType


def get_data_whatcd(ai, genretags):
    
    def query(action, **args):
        params = {'action': action}
        params.update(args)
        r = session.get('https://what.cd/ajax.php', params=params)
        j = json.loads(r.content)
        if j['status'] != 'success':
            raise Exception("unsuccessful response from what.cd")
        return j['response']
        
    def add_tags(genretags, tags):
        tags = list(tags)
        topcount = int(tags[0]['count']) + 1
        for tag in tags:
            if tag['name'] not in ['staff.picks', 'freely.available', 'vanity.house']:
                genretags.add(string.replace(tag['name'], '.', ' '), int(tag['count']) / topcount * SM_WHATCD)
    
    def interactive(data):
        print "Multiple releases found on What.CD, please choose the right one (0 to skip):"
        for i in range(len(data)):
            print "#%d: %s - %s [%d] [%s]" % (i + 1, data[i]['artist'], data[i]['groupName'], data[i]['groupYear'], data[i]['releaseType'])
        while True:
            try: c = int(raw_input("Choose Release #: "))
            except: c = None
            if c in range(len(data) + 1):
                break
        return None if c == 0 else data[c - 1]
    
    releaseType = None
    
    try:
        if not ai.va:
            data = query('artist', id=0, artistname=searchstr(ai.artist))
            if data.has_key('tags'):
                tags = sorted(data['tags'], key=operator.itemgetter('count'), reverse=True)
                add_tags(genretags, tags)
        
        data = query('browse', searchstr=searchstr(ai.album if ai.va else ai.artist + ' ' + ai.album), **{'filter_cat[1]':1})['results']
        
        if len(data) > 1 and args.interactive:
            data = interactive(data)
        elif len(data) == 1:
            data = data[0]
        else:
            data = None
            
        if data:
            tags = []
            for tag in data['tags']:
                tags.append({'name': tag, 'count': (0.85 ** (len(tags) - 1))})
            add_tags(genretags, tags)
            releaseType = data['releaseType']

    except Exception, e:
        print "ERROR with What.CD!", e

    return releaseType


def get_data_lastfm(ai, genretags):
    
    def query(method, **args):
        params = {'api_key': conf.lastfm_apikey, 'format': 'json', 'method': method}
        params.update(args)
        r = session.get('http://ws.audioscrobbler.com/2.0/', params=params) 
        j = json.loads(r.content)
        return j
     
    def add_tags(genretags, tags):
        badtags = [ai.album.lower(), ai.artist.lower(), 'albums i dont own yet', 'albums i own',
                   'albums i want', 'favorite album', 'favorite', 'lieblingssongs', 'own it', 'my albums'
                   'owned cds', 'seen live', 'wishlist', 'best of', 'laidback-221', 'number one']
        if tags.__class__ is not list:
            tags = [tags]
        topcount = int(tags[0]['count']) + 1
        for tag in tags:
            if not get_close_matches(tag['name'].lower(), badtags, 1):
                genretags.add(tag['name'], int(tag['count']) / topcount * SM_LASTFM)
    
    try:
        if not ai.va:
            data = query('artist.gettoptags', artist=searchstr(ai.artist))
            if data.has_key('toptags') and data['toptags'].has_key('tag'):
                add_tags(genretags, data['toptags']['tag'])
        
        data = query('album.gettoptags', artist=searchstr('Various Artists' if ai.va else ai.artist), album=searchstr(ai.album))
        if data.has_key('toptags') and data['toptags'].has_key('tag'):
            add_tags(genretags, data['toptags']['tag'])
            
    except Exception, e:
        print "ERROR with Last.FM!", e


def get_data_mbrainz(ai, genretags):
    
    def add_tags(genretags, tags):
        tags = list(tags)
        topcount = int(tags[0]['count']) + 1
        for tag in tags:
            genretags.add(tag['name'], int(tag['count']) / topcount * SM_MBRAIN)

    mbrainzids = [None, None, []]

    try:
        if not ai.va:
            r = musicbrainzngs.search_artists(artist=searchstr(ai.artist), limit=1)
            if r['artist-list']:
                artistid = r['artist-list'][0]['id']
                r = musicbrainzngs.get_artist_by_id(artistid, includes=['tags'])
                if r['artist'].has_key('tag-list'):
                    tags = sorted(r['artist']['tag-list'], key=operator.itemgetter('count'), reverse=True)
                    add_tags(genretags, tags)
                    
            r = musicbrainzngs.search_release_groups(artist=searchstr(ai.artist), release=searchstr(ai.album), limit=1)
        if ai.va:
            r = musicbrainzngs.search_release_groups(release=searchstr(ai.album), limit=1)
            
        if r['release-group-list']:
            releasegroupid = r['release-group-list'][0]['id']
            r = musicbrainzngs.get_release_group_by_id(releasegroupid, includes=['tags'])
            if r['release-group'].has_key('tag-list'):
                tags = sorted(r['release-group']['tag-list'], key=operator.itemgetter('count'), reverse=True)
                add_tags(genretags, tags)

    except:
        print "ERROR with MusicBrainz!"

    return mbrainzids


def get_data_discogs(ai, genretags):
    
    def query(thetype, **args):
        params = {'type': thetype}
        params.update(args)
        r = session.get('http://api.discogs.com/database/search', params=params) 
        j = json.loads(r.content)
        return j['results']
     
    def add_tags(genretags, tags):
        for tag in tags:
            genretags.add(tag, 0.85 ** (len(tags) - 1) * SM_DISCOG)
    
    try:
        data = query('master', release_title=searchstr(ai.album))
        if data:
            if data[0].has_key('style'):
                add_tags(genretags, data[0]['style'])
            if data[0].has_key('genre'):
                add_tags(genretags, data[0]['genre'])

    except Exception, e:
        print "ERROR with Discogs!", e



def liststr(mylist):
    return '%s' % ', '.join(map(str, mylist))


def searchstr(mystr):
    return re.sub(r'\[\W\S\]+', '', mystr)


def config_list(s):
    if s:
        return [i.strip() for i in s.split(',')]
    return []

def niceprint(s, color=None, bold=False):
    if bold:
        s = "\033[1m%s\033[0m" % s
    return s

def main():
    global args, conf, session
    
    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter,
                                     description='Improves genre-metadata of audio-files based on tags from various music-sites.')
    parser.add_argument('path', nargs='+',
                        help='folder(s) to scan')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='run verbose (more output)')
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help='dry-run (write nothing)')
    parser.add_argument('-i', '--interactive', action='store_true',
                        help='interactive mode')
    parser.add_argument('-r', '--tag-release', action='store_true',
                        help='tag release type from what.cd')
    parser.add_argument('-s', '--stats', action='store_true',
                        help='collect stats to written genres')
    parser.add_argument('-l', '--tag-limit', metavar='N', type=int,
                        help='max. number of genre tags', default=4)
    parser.add_argument('--no-whatcd', action='store_true',
                        help='disable lookup on What.CD')
    parser.add_argument('--no-lastfm', action='store_true',
                        help='disable lookup on Last.FM')
    parser.add_argument('--no-mbrainz', action='store_true',
                        help='disable lookup on MusicBrainz')
    parser.add_argument('--no-discogs', action='store_true',
                        help='disable lookup on Discogs')
    parser.add_argument('--config', default=os.path.expanduser('~/.whatlastgenre/config'),
                        help='location of the configuration file')
    '''
    parser.add_argument('--cache', default=os.path.expanduser('~/.whatlastgenre/cache'),
                        help='location of the cache')
    parser.add_argument('--no-cache', action='store_true',
                        help='disable cache feature')
    parser.add_argument('--clear-cache', action='store_true',
                        help='clear the cache')
    '''

    args = parser.parse_args()
    
    if args.no_whatcd and args.no_lastfm and args.no_mbrainz and args.no_discogs:
        print "Where do you want to get your data from? At least one source must be activated!"
        sys.exit()

    config = SafeConfigParser()
    try:
        open(args.config)
        config.read(args.config)
    except:
        if not os.path.exists(os.path.dirname(args.config)):
            os.makedirs(os.path.dirname(args.config))
        config.add_section('whatcd')
        config.set('whatcd', 'username', '')
        config.set('whatcd', 'password', '')
        config.add_section('lastfm')
        config.set('lastfm', 'apikey', '54bee5593b60d0a5bf379cedcad79052')
        config.add_section('genres')
        config.set('genres', 'whitelist', '')
        config.set('genres', 'blacklist', '')
        config.set('genres', 'uppercase', 'IDM, UK, US')
        config.set('genres', 'score_up', 'Trip-Hop')
        config.set('genres', 'score_down', 'Electronic, Rock, Metal, Alternative, Indie, Other, Other, Unknown, Unknown')
        config.write(open(args.config, 'w'))
        print "Please edit the configuration file: %s" % args.config
        sys.exit(2)

    conf = namedtuple('conf', '')
    conf.genre_whitelist = config_list(config.get('genres', 'whitelist'))
    conf.genre_blacklist = config_list(config.get('genres', 'blacklist'))
    conf.genre_uppercase = config_list(config.get('genres', 'uppercase'))
    conf.genre_score_up = config_list(config.get('genres', 'score_up'))
    conf.genre_score_down = config_list(config.get('genres', 'score_down'))
    conf.lastfm_apikey = config.get('lastfm', 'apikey')
    
    whatcd_user = config.get('whatcd', 'username')
    whatcd_pass = config.get('whatcd', 'password')
    
    if not (whatcd_user and whatcd_pass):
        print "No What.CD credentials specified. What.CD support disabled."
        args.no_whatcd = True
    
    if args.no_whatcd and args.tag_release:
        print "Can't tag release with What.CD support disabled. Release tagging disabled."
        args.tag_release = False
    
    if not conf.lastfm_apikey:
        print "No Last.FM apikey specified, Last.FM support disabled."
        args.no_lastfm = True
    
    session = requests.session()
    if not args.no_whatcd:
        session.post('https://what.cd/login.php', {'username': whatcd_user, 'password': whatcd_pass})

    if not args.no_mbrainz:
        musicbrainzngs.set_useragent("whatlastgenre", "0.1")
    
    ''' DEVEL Helper '''
    #args.verbose = True
    #args.dry_run = True
    #args.stats = True
    #args.tag_release = True
    #args.interactive = True
    #args.path.append('/home/foo/nobackup/test/')
    #args.path.append('/media/music/Alben/')
    #import random; args.path.append(os.path.join('/media/music/Alben', random.choice(os.listdir('/media/music/Alben'))))
    
    if args.stats:
        global stats
        stats = {}
    
    for path in args.path:
        scan_folder(path)
        
    if args.stats:
        print "Stats:",
        for tag, num in sorted(stats.iteritems(), key=lambda (tag, num): (num, tag), reverse=True):
            print "%s: %d," % (tag, num),
        print
        
    print "... all done!"

if __name__ == "__main__":
    main()
