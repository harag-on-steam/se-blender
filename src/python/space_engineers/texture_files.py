from collections import namedtuple
from enum import Enum
from itertools import groupby
import os
import re
import bpy

_RE_TEXTURE_TYPE = re.compile(
    r"(?P<de>Diffuse_?(?P<me1>Masked_?)?(?:Emissiv(?:e|ity)?)?|DE|(?P<me2>ME))|"
    r"(?P<ns>Normal_?(?:Specular(?:ity)?)|NS)|"
    r"(?P<cm>(?:(?:Base_?)?Color|Albedo)_?Metal(?:ness|ic)?|CM)|"
    r"(?P<ng>Normal_?Gloss(?:iness)?|NG)|"
    r"(?P<add>Add(?:_?Maps?|itional)?)|"
    r"(?P<alphamask>Alpha(?:Mask)?)"
)

_RE_TEXTURE_LABEL = re.compile(
    r"(?:" + _RE_TEXTURE_TYPE.pattern + r")_?(?:Tex(?:ture)?)?(?:\.\d+)?",
    re.IGNORECASE)

_RE_TEXTURE_FILENAME = re.compile(
    # basename is non-greedy so that the texture-type discriminator can be optional
    r"^(?P<basename>.+?)_?(?:" + _RE_TEXTURE_TYPE.pattern + r")?\.(?P<extension>[^.]+)$",
    re.IGNORECASE)

class TextureType(Enum):
    Diffuse = 'de'
    Normal = 'ns'
    ColorMetal = 'cm'
    NormalGloss = 'ng'
    AddMaps = 'add'
    Alphamask = 'alphamask'

TextureFileName = namedtuple('TextureFileName', ('filepath', 'basename', 'textureType', 'extension'))

def _textureTypeFromMatch(match) -> TextureType:
    if match is None:
        return None
    return next((t for t in TextureType if match.group(t.value)), None)

def textureTypeFromLabel(label: str) -> TextureType:
    return _textureTypeFromMatch(_RE_TEXTURE_LABEL.match(label))

def textureTypeFromObjectName(obj) -> TextureType:
    textureType = textureTypeFromLabel(obj.name)
    return textureType if textureType else textureTypeFromLabel(obj.label)

def imagesFromNodes(nodes):
    pairs = ((textureTypeFromObjectName(img), img) for img in nodes if isinstance(img, bpy.types.ShaderNodeTexImage))
    return {t : n for t, n in pairs if t}

def textureFileNameFromPath(filepath: str) -> TextureFileName:
    filename = os.path.basename(filepath)
    match = _RE_TEXTURE_FILENAME.match(filename)
    if not match:
        return None
    return TextureFileName(
        filepath = filepath,
        basename = match.group('basename').lower(),
        textureType = _textureTypeFromMatch(match),
        extension = match.group('extension').lower(),
    )

def textureFilesFromPath(path: str, acceptedExtensions={'dds'}) -> map:
    files = (textureFileNameFromPath(os.path.join(path, f)) for f in os.listdir(path))
    files = filter(lambda f: f and f.textureType and f.extension in acceptedExtensions, files)
    # for files with equal basename and equivalent texture-type this chooses the longest filename (as most descriptive)
    files = sorted(files, key=lambda f: (f.basename, len(f.filepath)))
    files = groupby(files, lambda f: f.basename)
    files = {basename : {f.textureType : f for f in groupedFiles} for basename, groupedFiles in files}
    return files
