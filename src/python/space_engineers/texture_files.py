from collections import namedtuple
from enum import Enum
from itertools import groupby
import os
import re
import bpy

_RE_DIFFUSE = re.compile(r"_[dm]e\.dds$", re.IGNORECASE)
_RE_NORMAL = re.compile(r"_ns\.dds$", re.IGNORECASE)

_RE_TEXTURE_TYPE = re.compile(
    r"(?P<de>Diffuse_?(?P<me1>Masked_?)?(?:Emissiv(?:e|ity)?)?|DE|(?P<me2>ME))|"
    r"(?P<ng>Normal_?Gloss(?:iness)?|NG)|" # needs to be before "Normal" due to non-optional suffix "Gloss"
    r"(?P<ns>Normal_?(?:Specular(?:ity)?)?|NS)|"
    r"(?P<cm>(?:(?:Base_?)?Color|Albedo)_?Metal(?:ness|ic)?|CM)|"
    r"(?P<add>Add(?:_?Maps?|itional)?)|"
    r"(?P<alphamask>Alpha(?:Mask)?)",
    re.IGNORECASE
)

_RE_TEXTURE_LABEL = re.compile(
    r"(?:" + _RE_TEXTURE_TYPE.pattern + r")_?(?:Tex(?:ture)?)?(?:\.\d+)?",
    re.IGNORECASE)

_RE_TEXTURE_FILENAME = re.compile(
    # basename is non-greedy so that the texture-type discriminator can be optional
    r"^(?P<basename>.+?)_?(?:" + _RE_TEXTURE_TYPE.pattern + r")?\.(?P<extension>[^.]+)$",
    re.IGNORECASE)

class TextureType(Enum):
    # NameInParameterXml = 'file-suffix'
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

def imageNodes(nodes):
    """
    Extracts a map {TextureType -> bpy.types.ShaderNodeTexImage} from the given nodes.
    The map will only contain keys for which there actually are texture-nodes.
    The nodes do not necessarily have images, use imagesFromNodes() for that.
    """
    pairs = ((textureTypeFromObjectName(img), img) for img in nodes if isinstance(img, bpy.types.ShaderNodeTexImage))
    return {t : n for t, n in pairs if t}

def imagesFromNodes(nodes):
    """
    Extracts a map {TextureType -> bpy.types.Image} from the given nodes.
    The map will only contain keys for which there actually are images.
    """
    return {t : n.image for t, n in imageNodes(nodes).items() if n.image}

def textureFileNameFromPath(filepath: str) -> TextureFileName:
    """
    Decomposes the filename of a given filepath into basename, textureType and extension.
    The textureType might be None if it could not be determined.
    The extension is None if the file doesn't have one.
    basename and extension always are in lower-case recardless of the case in filepath.
    """
    filename = os.path.basename(filepath)
    match = _RE_TEXTURE_FILENAME.match(filename)
    if not match:
        return TextureFileName(filepath, filename, None, None)
    return TextureFileName(
        filepath = filepath,
        basename = match.group('basename').lower(),
        textureType = _textureTypeFromMatch(match),
        extension = match.group('extension').lower(),
    )

def textureFilesFromPath(dirpath: str, acceptedExtensions={'dds'}) -> dict:
    """
    Builds a map of maps {basename -> {TextureType -> TextureFileName}} for all the files in the given directory.
    Files for which no Texture
    """
    files = (textureFileNameFromPath(os.path.join(dirpath, f)) for f in os.listdir(dirpath))
    files = filter(lambda f: f and f.textureType and f.extension in acceptedExtensions, files)
    # for files with equal basename and equivalent texture-type this chooses the longest filename (as most descriptive)
    files = sorted(files, key=lambda f: (f.basename, len(f.filepath)))
    files = groupby(files, lambda f: f.basename)
    files = {basename : {f.textureType : f for f in groupedFiles} for basename, groupedFiles in files}
    return files

def imageFromFilePath(filepath):
    """
    Provides a bpy.types.Image for the given filepath.
    The function checks if there is an existing Image with such a filepath and loads a new one if there isn't.
    """
    filepath = bpy.path.abspath(filepath)
    for image in bpy.data.images:
        if image.filepath and os.path.samefile(bpy.path.abspath(image.filepath), filepath):
            return image
    image = bpy.data.images.load(bpy.path.relpath(filepath))
    return image

def matchingFileNamesFromFilePath(filepath):
    """
    Provides a map {TextureType -> bpy.types.Image} for images that reside in the same directory as
    the file given by filepath and that share the same basename.
    """
    filepath = bpy.path.abspath(filepath)
    textureFileName = textureFileNameFromPath(filepath)
    if not textureFileName:
        return {}

    allFilesInDir = textureFilesFromPath(os.path.dirname(filepath))
    matchingFiles = allFilesInDir.get(textureFileName.basename, None)
    return matchingFiles if matchingFiles else {}
