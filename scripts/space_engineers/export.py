from collections import namedtuple
import io
import os
from os.path import join
import re
from string import Template
import subprocess
import tempfile
from xml.etree import ElementTree
import bpy
import shutil

from .mwmbuilder import mwmbuilder_xml, material_xml
from space_engineers.mount_points import mount_point_definitions, mount_points_xml
from .utils import scaleUni, layer_bits, layer_bit
from .types import data
from .fbx import save_single

from bpy_extras.io_utils import axis_conversion, ExportHelper

FWD = 'Z'
UP = 'Y'
MATRIX_NORMAL = axis_conversion(to_forward=FWD, to_up=UP).to_4x4()
MATRIX_SCALE_DOWN = scaleUni(0.2) * MATRIX_NORMAL

class StdoutOperator():
    def report(self, type, message):
        print(message)

STDOUT_OPERATOR = StdoutOperator()

def export_fbx(scene, filepath, objects, scale_down=False, operator=STDOUT_OPERATOR):
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

def fbx_to_hkt(srcfile, dstfile):
    fbx_importer = bpy.context.user_preferences.addons['space_engineers'].preferences.havokFbxImporter
    if not fbx_importer:
        raise FileNotFoundError("Havok FBX Importer is not configured")

    fbx_importer = os.path.normpath(bpy.path.abspath(fbx_importer))
    if not os.path.isfile(fbx_importer):
        raise FileNotFoundError("Havok FBX Importer: no such file %s" % (fbx_importer))

    return subprocess.check_output(
        [fbx_importer, srcfile, dstfile],
        stderr=subprocess.STDOUT)

from .havok_options import HAVOK_OPTION_FILE_CONTENT

def hkt_filter(srcfile, dstfile, options=HAVOK_OPTION_FILE_CONTENT):
    filter_manager = bpy.context.user_preferences.addons['space_engineers'].preferences.havokFilterMgr
    if not filter_manager:
        raise FileNotFoundError("Havok Filter Manager is not configured")

    filter_manager = os.path.normpath(bpy.path.abspath(filter_manager))
    if not os.path.isfile(filter_manager):
        raise FileNotFoundError("Havok Filter Manager: no such file %s" % (filter_manager))

    hko = tempfile.NamedTemporaryFile(mode='wt', prefix='space_engineers_', suffix=".hko", delete=False)
    try:
        with hko.file as f:
            f.write(options)

        return subprocess.check_output(
            [filter_manager, '-t', '-s', hko.name, '-p', dstfile, srcfile],
            stderr=subprocess.STDOUT)
    finally:
        os.remove(hko.name)

def write_to_log(logfile, content):
    with open(logfile, 'wb') as log:
        log.write(content)

def write_pretty_xml(etree_root_element, filepath):
    # what a hack...
    import xml.dom.minidom as dom
    xmlString = ElementTree.tostring(etree_root_element, 'utf-8')
    minidom = dom.parseString(xmlString)
    prettyXml = minidom.toprettyxml(encoding='utf-8')
    with open(filepath, mode='wb') as file:
        file.write(prettyXml)

def mwmbuilder(srcfile, dstfile):
    mwmbuilder = bpy.context.user_preferences.addons['space_engineers'].preferences.mwmbuilder
    if not mwmbuilder:
        raise FileNotFoundError("MWMBuilder is not configured")

    mwmbuilder = os.path.normpath(bpy.path.abspath(mwmbuilder))
    if not os.path.isfile(mwmbuilder):
        raise FileNotFoundError("MWMBuilder: no such file %s" % (mwmbuilder))

    cmdline = [mwmbuilder, '/s:'+srcfile] #, '/o:'+os.path.dirname(dstfile)]
    return subprocess.check_output(cmdline, stderr=subprocess.STDOUT)

class Names:
    subtypeid = '${blockname}_${blocksize}'
    block_pair_name = '${blockname}'
    main = '${blockname}_${blocksize}'
    construction = '${blockname}_${blocksize}_Construction${n}'
    icon = 'Textures/Icons/${blockname}'

class ExportSet:
    def __init__(self, layer_mask_bits, filename):
        self.layer_mask_bits = layer_mask_bits
        self.filename_template = filename
        self.objects = []

    def test(self, ob):
        return (layer_bits(ob.layers) & self.layer_mask_bits) != 0

    def collect(self, ob):
        self.objects.append(ob)

class MwmSet(ExportSet):
    def __init__(self, layer_mask_bits, filename):
        super().__init__(layer_mask_bits, filename)
        self.materials = set()

    def collect(self, ob):
        super().collect(ob)
        self.materials |= {slot.material for slot in ob.material_slots if slot.material}

    def export(self, output_dir, scene, block_name, block_size, scale_down, havok_file=None, run_mwmbuilder=True):
        basename = Template(self.filename_template).substitute(blockname = block_name, blocksize = block_size)

        fbxfile = basename + '.fbx'
        paramsfile = basename + '.xml'
        havokfile = basename + '.hkt'
        mwmfile = basename + '.mwm'

        if havok_file:
            if havok_file != havokfile:
                shutil.copy2(join(output_dir, havok_file), join(output_dir, havokfile))

        paramsxml = mwmbuilder_xml(scene, (material_xml(mat) for mat in self.materials))
        write_pretty_xml(paramsxml, join(output_dir, paramsfile))

        export_fbx(scene, join(output_dir, fbxfile), self.objects, scale_down)

        try:
            if (run_mwmbuilder):
                out = mwmbuilder(join(output_dir, fbxfile), join(output_dir, mwmfile))
                write_to_log(join(output_dir, mwmfile) + '.log', out)
        except subprocess.CalledProcessError as e:
            write_to_log(join(output_dir, mwmfile) + '.log', e.output)
            raise

        return mwmfile

class HavokSet(ExportSet):
    def test(self, ob):
        return super().test(ob) and ob.rigid_body

    def export(self, output_dir, scene, block_name, block_size, scale_down):

        filename = Template(self.filename_template).substitute(blockname = block_name, blocksize = block_size) + '.hkt'
        havokfile = os.path.join(output_dir, filename)

        if len(self.objects) == 0:
            write_to_log(havokfile + '.convert.log', b"no collision available for export")
            return None

        fbxfile = os.path.join(output_dir, filename) + '.fbx'
        export_fbx(scene, fbxfile, self.objects, scale_down)

        try:
            out = fbx_to_hkt(fbxfile, havokfile)
            write_to_log(havokfile + '.convert.log', out)
        except subprocess.CalledProcessError as e:
            write_to_log(havokfile + '.convert.log', e.output)
            raise

        try:
            out = hkt_filter(havokfile, havokfile)
            write_to_log(havokfile + '.filter.log', out)
        except subprocess.CalledProcessError as e:
            write_to_log(havokfile + '.filter.log', e.output)
            raise

        return filename

class MountPointSet(ExportSet):
    def export(self, output_dir, scene, block_name, block_size, model, construction_models):
        d = data(scene)

        block = ElementTree.Element('Definition')

        id = ElementTree.SubElement(block, 'Id')
        subtypeId = ElementTree.SubElement(id, 'SubtypeId')
        subtypeId.text = Template(Names.subtypeid).safe_substitute(blockname=block_name, blocksize=block_size)

        icon = ElementTree.SubElement(block, 'Icon')
        icon.text = Template(Names.icon).safe_substitute(blockname=block_name, blocksize=block_size) + '.dds'

        ElementTree.SubElement(block, 'CubeSize').text = block_size
        ElementTree.SubElement(block, 'BlockTopology').text = 'TriangleMesh'

        x, z, y = d.block_dimensions # z and y switched on purpose; y is up, z is forward in SE
        ElementTree.SubElement(block, 'Size', x=str(x), y=str(y), z=str(z))

        ElementTree.SubElement(block, 'ModelOffset', x='0', y='0', z='0')

        ElementTree.SubElement(block, 'Model').text = model

        numConstr = len(construction_models)
        if numConstr > 0:
            constr = ElementTree.SubElement(block, 'BuildProgressModels')
            for i, c in enumerate(construction_models):
                upperBound = "%.2f" % (1.0 * (i+1) / numConstr)
                ElementTree.SubElement(constr, 'Model', BuildPercentUpperBound=upperBound, File=c)

        mountpoints = mount_point_definitions(self.objects)
        if len(mountpoints) > 0:
            block.append(mount_points_xml(mountpoints))

        blockPairName = ElementTree.SubElement(block, 'BlockPairName')
        blockPairName.text = Template(Names.block_pair_name).safe_substitute(blockname=block_name, blocksize=block_size)

        filename = Template(Names.main).safe_substitute(blockname=block_name, blocksize=block_size) + '.blockdef.xml'

        write_pretty_xml(block, join(output_dir, filename))

        return filename

# mapping (block_size) -> (name, scale_down)
SIZES = {
    'LARGE' : [('Large', False)],
    'SMALL' : [('Small', False)],
    'SCALE_DOWN' : [('Large', False), ('Small', True)]
}

class BlockExport:
    def __init__(self, scene):
        self.blockname = scene.name

        d = data(scene)

        self.havok = HavokSet(layer_bits(d.physics_layers), Names.main)
        self.mp = MountPointSet(layer_bits(d.mount_points_layers), Names.main)

        self.main = MwmSet(layer_bits(d.main_layers), Names.main)

        constr_bits = [layer_bit(i) for i, layer in enumerate(d.construction_layers) if layer]
        self.constr = [MwmSet( bits, Template(Names.construction).safe_substitute(n = i+1))
            for i, bits in enumerate(constr_bits)]

        sets = [self.havok, self.mp, self.main] + self.constr

        for ob in scene.objects:
            for set in sets:
                if set.test(ob):
                    set.collect(ob)

    def export(self, scene, output_dir, block_name=None, run_mwmbuilder=True):
        d = data(scene)
        outDir = os.path.normpath(bpy.path.abspath(output_dir))
        blockName = block_name if block_name else scene.name

        for blockSize, scaleDown in SIZES[d.block_size]:
            havokfile = self.havok.export(outDir, scene, blockName, blockSize, scaleDown)
            modelFile = self.main.export(outDir, scene, blockName, blockSize, scaleDown, havokfile, run_mwmbuilder)
            constrFiles = [
                c.export(outDir, scene, blockName, blockSize, scaleDown, havokfile, run_mwmbuilder)
                    for c in self.constr
            ]
            deffile = self.mp.export(outDir, scene, blockName, blockSize, modelFile, constrFiles)

def export_block(scene, output_dir, block_name=None, run_mwmbuilder=True):
    BlockExport(scene).export(scene, output_dir, block_name, run_mwmbuilder)

class ExportSceneAsBlock(bpy.types.Operator):
    bl_idname = "export_scene.space_engineers_block"
    bl_label = "Export Space Engineers Block"
    bl_description = "Exports the current scene as a block."

    directory = bpy.props.StringProperty(subtype='DIR_PATH')

    all_scenes = bpy.props.BoolProperty(
        name="All Scenes",
        description="Export all scenes that are marked as blocks.")
    skip_mwmbuilder = bpy.props.BoolProperty(
        name="Skip mwmbuilder",
        description="Export intermediary files but do not run them through mwmbuilder")

    @classmethod
    def poll(self, context):
        if not context.scene:
            return False

        d = data(context.scene)
        return d and d.is_block

    def invoke(self, context, event):
        if not self.directory:
            self.directory = os.path.dirname(context.blend_data.filepath)

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        lay = self.layout

        col = lay.column()
        col.prop(self, "all_scenes")
        col.prop(self, "skip_mwmbuilder")

    def execute(self, context):
        try:
            if self.all_scenes:
                scenes = [scene for scene in bpy.data.scenes if data(scene).is_block]
            else:
                scenes = [context.scene]

            wm = context.window_manager
            wm.progress_begin(0, len(scenes))
            try:
                for i, scene in enumerate(scenes):
                    export_block(scene, self.directory, scene.name, run_mwmbuilder=not self.skip_mwmbuilder)
                    wm.progress_update(i)
            finally:
                 wm.progress_end()

        except FileNotFoundError as e: # raised when the addon preferences are missing some tool paths
            self.report({'ERROR'}, "Configuration error: %s" % e)

        except subprocess.CalledProcessError as e:
            self.report({'ERROR'}, "An external tool failed, check generated logs: %s" % e)

        return {'FINISHED'}
