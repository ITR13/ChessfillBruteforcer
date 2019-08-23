#!/usr/bin/env python3
# stolen from https://stackoverflow.com/questions/7894384/python-get-url-path-sections

import urllib.parse
import sys
import posixpath
import ntpath
import json

def path_parse( path_string, *, normalize = True, module = posixpath ):
    result = []
    if normalize:
        tmp = module.normpath( path_string )
    else:
        tmp = path_string
    while tmp != "/":
        ( tmp, item ) = module.split( tmp )
        result.insert( 0, item )
    return result

def dump_array( array ):
    string = "[ "
    for index, item in enumerate( array ):
        if index > 0:
            string += ", "
        string += "\"{}\"".format( item )
    string += " ]"
    return string

def test_url( url, *, normalize = True, module = posixpath ):
    url_parsed = urllib.parse.urlparse( url )
    path_parsed = path_parse( urllib.parse.unquote( url_parsed.path ),
        normalize=normalize, module=module )
    sys.stdout.write( "{}\n  --[n={},m={}]-->\n    {}\n".format( 
        url, normalize, module.__name__, dump_array( path_parsed ) ) )