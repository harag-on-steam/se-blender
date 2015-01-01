import re
from string import Template
import bpy
import bpy.path
import os.path

from .utils import scaleUni, layer_bits, layer_bit, first
from .types import data
from .fbx import save_single

from bpy_extras.io_utils import axis_conversion

FWD = 'Z'
UP = 'Y'
MATRIX_NORMAL = axis_conversion(to_forward=FWD, to_up=UP).to_4x4()
MATRIX_SCALE_DOWN = scaleUni(0.2) * MATRIX_NORMAL

SIZES = {
    'LARGE' : [('Large', False)],
    'SMALL' : [('Small', False)],
    'SCALE_DOWN' : [('Large', False), ('Small', True)]
}

class FilePaths:
    main = '${blockname}_${blocksize}'
    construction = '${blockname}_${blocksize}_Construction${n}'
    havok = '${blockname}_${blocksize}_Havok'

class ExportSet:
    def __init__(self, layer_mask_bits, filename, **kwargs):
        self.filename_template = Template(filename).safe_substitute(kwargs)
        self.filename_params = kwargs
        self.layer_mask_bits = layer_mask_bits
        self.materials = set()
        self.objects = []

    def test(self, ob, ob_layer_bits):
        return (ob_layer_bits & self.layer_mask_bits) != 0

    def collect(self, ob):
        self.objects.append(ob)

    def __str__(self):
        return "%s(" \
               "\n\tfilename_template=%s" \
               "\n\tfilename_params=%s" \
               "\n\tlayers=%s" \
               "\n\tmaterials=%s" \
               "\n\tobjects=%s" \
               ")" \
        % (
            self.__class__.__name__,
            self.filename_template,
            self.filename_params,
            bin(self.layer_mask_bits),
            [mat.name for mat in self.materials],
            [ob.name for ob in self.objects],
        )

class MwmSet(ExportSet):
    def collect(self, ob):
        super().collect(ob)
        self.materials |= {slot.material for slot in ob.material_slots if slot.material}

class HavokSet(ExportSet):
    def test(self, ob, ob_layer_bits):
        return super().test(ob, ob_layer_bits) and ob.rigid_body

class MountPointSet(ExportSet):
    pass

def export_sets(scene):
    d = data(scene)

    havok = HavokSet(layer_bits(d.physics_layers), '${blockname}_${blocksize}_Havok')
    mp = MountPointSet(layer_bits(d.mount_points_layers), None)

    main = MwmSet(layer_bits(d.main_layers), '${blockname}_${blocksize}')
    constr_bits = [layer_bit(i) for i, layer in enumerate(d.construction_layers) if layer]
    constr = [MwmSet( bits, '${blockname}_${blocksize}_Construction${n}', n=i+1 )
        for i, bits in enumerate(constr_bits)]

    sets = [havok, mp, main] + constr

    for ob in scene.objects:
        ob_layer_bitset = layer_bits(ob.layers)
        for set in sets:
            if set.test(ob, ob_layer_bitset):
                set.collect(ob)

    return sets


def export_fbx(operator, scene, filepath, objects, scale_down):
    return save_single(
        operator,
        scene,
        filepath=filepath,
        context_objects=objects,
        object_types={'MESH', 'EMPTY'},
        global_matrix=MATRIX_SCALE_DOWN if scale_down else MATRIX_NORMAL,
        axis_forward=FWD,
        axis_up=UP,
        bake_space_transform=True,
        use_mesh_modifiers=True,
        mesh_smooth_type='OFF',
    )


# ------------------------------------- mwmbuilder xml ---------------------------------------- #

from xml.etree import ElementTree

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
    return os.path.normpath(se_dir) + os.path.sep + "Content" + os.path.sep

def derive_texture_path(filepath):
    def is_in_subpath(relpath):
        return not relpath.startswith('..') and not os.path.isabs(relpath)

    image_path = os.path.normpath(bpy.path.abspath(filepath))

    relative_to_se = os.path.relpath(image_path, se_content_dir())
    if is_in_subpath(relative_to_se):
        return relative_to_se

    relative_to_basedir = os.path.relpath(image_path, texture_basedir())
    if is_in_subpath(relative_to_basedir):
        return relative_to_basedir

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

def material_xml(mat):
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

def mwmbuilder_xml(scene, materials):
    d = data(scene)
    e = ElementTree.Element("Model", Name=scene.name)

    def param(name, value):
        se = ElementTree.SubElement(e, 'Parameter', Name=name)
        if value:
            se.text = value

    param("RescaleFactor", "1.0")
    param("RescaleToLengthInMeters", "false")
    param("Centered", "false")
    param("SpecularPower", _floatstr(d.block_specular_power))
    param("SpecularShininess", _floatstr(d.block_specular_shininess))

    for mat in materials:
        e.append(material_xml(mat))

    # TODO other mwmbuilder.xml parameters:
    # <Parameter Name="PatternScale">4</Parameter>
    # <BoneGridSize>2.5</BoneGridSize>
    # <BoneMapping>
    #    <Bone X="0" Y="0" Z="0" />
    # <LOD Distance="300">
    #    <Model>Models\Cubes\large\Assembler_LOD1</Model>

    return e
