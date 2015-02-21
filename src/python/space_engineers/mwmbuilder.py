import os
import re
import bpy

from xml.etree import ElementTree
from .types import data

_RE_DIFFUSE = re.compile(r"_[dm]e\.dds$", re.IGNORECASE)
_RE_NORMAL = re.compile(r"_ns\.dds$", re.IGNORECASE)

def diffuse_texture_path(material):
    for slot in material.texture_slots:
        if not slot or not slot.texture or not slot.texture.image:
            continue

        filepath = slot.texture.image.filepath
        if slot.use_map_color_diffuse or _RE_DIFFUSE.search(filepath):
            return filepath

    return None

def normal_texture_path(material):
    for slot in material.texture_slots:
        if not slot or not slot.texture or not slot.texture.image:
            continue

        filepath = slot.texture.image.filepath
        if slot.use_map_normal or _RE_NORMAL.search(filepath):
            return filepath

    return None

def texture_basedir():
    # TODO for now assume textures are relative to the .blend file
    return os.path.normpath(bpy.path.abspath('//'))

def se_content_dir():
    se_dir = bpy.path.abspath(bpy.context.user_preferences.addons['space_engineers'].preferences.seDir)
    if not se_dir:
        return texture_basedir() # fallback, if unset
    return os.path.join(os.path.normpath(se_dir), "Content")

def derive_texture_path(filepath):
    def is_in_subpath(relpath):
        return not relpath.startswith('..') and not os.path.isabs(relpath)

    image_path = os.path.normpath(bpy.path.abspath(filepath))

    try:
        relative_to_se = os.path.relpath(image_path, se_content_dir())
        if is_in_subpath(relative_to_se):
            return relative_to_se
    except ValueError:
        pass

    try:
        relative_to_basedir = os.path.relpath(image_path, texture_basedir())
        if is_in_subpath(relative_to_basedir):
            return relative_to_basedir
    except ValueError:
        pass

    return image_path

def derive_texture_paths(material):
    diffuse = diffuse_texture_path(material)
    if diffuse:
        diffuse = derive_texture_path(diffuse)

    normal = normal_texture_path(material)
    if normal:
        normal = derive_texture_path(normal)
        if diffuse and _RE_DIFFUSE.sub('_ns.dds', diffuse).lower() == normal.lower():
            normal = '' # with a xyz_de.dds diffuse texture the xyz_ns.dds texture can be defaulted to <NormalTexture/>
    else:
        if diffuse and _RE_DIFFUSE.search(diffuse):
            normal = '' # assume the _ns.dds just is not configured in Blender

    return (diffuse, normal)

def _floatstr(f):
    return str(round(f, 2))

def material_xml(settings, mat):
    d = data(mat)
    e = ElementTree.Element("Material", Name=mat.name)

    def param(name, value):
        se = ElementTree.SubElement(e, 'Parameter', Name=name)
        if value:
            se.text = value

    param("Technique", d.technique)
    param("SpecularIntensity", _floatstr(d.specular_intensity))
    param("SpecularPower", _floatstr(d.specular_power))

    if 'GLASS' == d.technique:
        param("DiffuseColorX", '255')
        param("DiffuseColorY", '255')
        param("DiffuseColorZ", '255')
        param("GlassMaterialCCW", d.glass_material_ccw)
        param("GlassMaterialCW", d.glass_material_cw)
        param("GlassSmooth", str(d.glass_smooth))
    else:
        r, g, b = d.diffuse_color
        param("DiffuseColorX", str(int(255 * r)))
        param("DiffuseColorY", str(int(255 * g)))
        param("DiffuseColorZ", str(int(255 * b)))

    textures = derive_texture_paths(mat)

    if textures[0]:
        param("DiffuseTexture", textures[0])
    else:
        e.append(ElementTree.Comment("material has no diffuse-texture"))

    if None != textures[1]: # normal-texture might be defaulted ('')
        param("NormalTexture", textures[1])
    else:
        e.append(ElementTree.Comment("material has no normal-texture"))

    return e

def lod_xml(settings, lodMwmFile: str, lodDistance: int):
    # TODO <LOD ... RenderQuality="EXTREME, HIGH">
    e = ElementTree.Element("LOD", Distance=str(lodDistance))
    em = ElementTree.SubElement(e, "Model")
    em.text = settings.template(settings.names.modelpath, modelfile=os.path.basename(lodMwmFile))
    return e

def mwmbuilder_xml(settings, material_elements, lod_elements):
    d = data(settings.scene)
    e = ElementTree.Element("Model", Name=settings.blockname)

    def param(name, value):
        se = ElementTree.SubElement(e, 'Parameter', Name=name)
        if value:
            se.text = value

    rescalefactor = '1.0'
    if settings.isOldMwmbuilder:
        rescalefactor = '0.002' if settings.scaleDown else '0.01'

    param("RescaleFactor", rescalefactor)
    param("RescaleToLengthInMeters", "false")
    param("Centered", "false")
    param("SpecularPower", _floatstr(d.block_specular_power))
    param("SpecularShininess", _floatstr(d.block_specular_shininess))

    for mat in material_elements:
        e.append(mat)

    for lod in lod_elements:
        e.append(lod)

    # TODO other mwmbuilder.xml parameters:

    # <Parameter Name="PatternScale">4</Parameter>
    # <BoneGridSize>2.5</BoneGridSize>
    # <BoneMapping>
    #    <Bone X="0" Y="0" Z="0" />

    return e
