from collections import OrderedDict
import os
import bpy
from xml.etree import ElementTree
from .texture_files import TextureType
from .types import data, SEMaterialInfo, rgb


def se_content_dir(settings):
    se_dir = bpy.path.abspath(bpy.context.user_preferences.addons['space_engineers'].preferences.seDir)
    if not se_dir:
        return settings.baseDir # fallback, if unset
    return os.path.join(os.path.normpath(se_dir), "Content")

def derive_texture_path(settings, filepath):
    def is_in_subpath(relpath):
        return not relpath.startswith('..') and not os.path.isabs(relpath)

    image_path = os.path.normpath(bpy.path.abspath(filepath))

    try:
        relative_to_se = os.path.relpath(image_path, se_content_dir(settings))
        if is_in_subpath(relative_to_se):
            return relative_to_se
    except ValueError:
        pass

    try:
        relative_to_basedir = os.path.relpath(image_path, settings.baseDir)
        if is_in_subpath(relative_to_basedir):
            return relative_to_basedir
    except ValueError:
        pass

    return image_path

def _floatstr(f):
    return str(round(f, 2))

def material_xml(settings, mat):
    d = data(mat)
    e = ElementTree.Element("Material", Name=mat.name)
    m = SEMaterialInfo(mat)

    def param(name, value):
        se = ElementTree.SubElement(e, 'Parameter', Name=name)
        if value:
            se.text = value

    param("Technique", d.technique)
    param("SpecularIntensity", _floatstr(m.specularIntensity))
    param("SpecularPower", _floatstr(m.specularPower))

    if 'GLASS' == d.technique:
        param("DiffuseColorX", '255')
        param("DiffuseColorY", '255')
        param("DiffuseColorZ", '255')
        param("GlassMaterialCCW", d.glass_material_ccw)
        param("GlassMaterialCW", d.glass_material_cw)
        param("GlassSmooth", str(d.glass_smooth))
    else:
        r, g, b = rgb(m.diffuseColor)
        param("DiffuseColorX", str(int(255 * r)))
        param("DiffuseColorY", str(int(255 * g)))
        param("DiffuseColorZ", str(int(255 * b)))

    # only for legacy materials
    if m.couldDefaultNormalTexture and not TextureType.Normal in m.images:
        m.images[TextureType.Normal] = ''

    for texType in TextureType:
        filepath = m.images.get(texType, None)
        if not filepath is None:
            param(texType.name + "Texture", derive_texture_path(settings, filepath))
        else:
            e.append(ElementTree.Comment("material has no %sTexture" % texType.name))

    return e

def lod_xml(settings, lodMwmFile: str, lodDistance: int, renderQualities:iter=None):
    e = ElementTree.Element("LOD")
    attrib = OrderedDict()
    attrib['Distance'] = str(lodDistance)
    if not renderQualities is None:
        attrib['RenderQuality'] = ', '.join(renderQualities)
    e.attrib = attrib

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
