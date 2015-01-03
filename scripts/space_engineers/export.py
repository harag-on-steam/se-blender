from collections import namedtuple
from functools import partial
import hashlib
import io
import os
from os.path import join, basename
import re
from string import Template
import string
import subprocess
import tempfile
from xml.etree import ElementTree
import bpy
import shutil

from .mwmbuilder import mwmbuilder_xml, material_xml
from space_engineers.mount_points import mount_point_definitions, mount_points_xml
from .utils import scaleUni, layer_bits, layer_bit, md5sum
from .types import data
from .fbx import save_single

from bpy_extras.io_utils import axis_conversion, ExportHelper

class StdoutOperator():
    def report(self, type, message):
        print(message)

STDOUT_OPERATOR = StdoutOperator()

# mwmbuilder from Space Engineers 01.051
OLD_MWMBUILDER_MD5 = '261163f6d3743d28fede7944b2b0949a'

def tool_path(propertyName, displayName, toolPath=None):
    if None == toolPath:
        toolPath = getattr(bpy.context.user_preferences.addons['space_engineers'].preferences, propertyName)

    if not toolPath:
        raise FileNotFoundError("%s is not configured", (displayName))

    toolPath = os.path.normpath(bpy.path.abspath(toolPath))
    if not os.path.isfile(toolPath):
        raise FileNotFoundError("%s: no such file %s" % (displayName, toolPath))

    return toolPath

def write_to_log(logfile, content, cmdline=None, cwd=None, loglines=[]):
    with open(logfile, 'wb') as log:
        if cwd:
            str = "Running from: %s \n" % (cwd)
            log.write(str.encode('utf-8'))

        if cmdline:
            str = "Command: %s \n" % (" ".join(cmdline))
            log.write(str.encode('utf-8'))

        for line in loglines:
            log.write(line.encode('utf-8'))
            log.write(b"\n")

        log.write(content)

class Names:
    subtypeid = '${blockname}_${blocksize}'
    blockpairname = '${blockname}'
    main = '${blockname}_${blocksize}'
    construction = '${blockname}_${blocksize}_Construction${n}'
    icon = 'Textures\Icons\${blockname}.dds'
    modelpath = '${modeldir}${modelfile}'

class ExportSettings:
    def __init__(self, scene, outputDir):
        d = data(scene)

        self.scene = scene
        self.outputDir = os.path.normpath(bpy.path.abspath(outputDir))
        self.blockname = scene.name
        self.operator = STDOUT_OPERATOR
        self.isLogToolOutput = True
        self.isRunMwmbuilder = True
        self.modeldir = 'Models\\'

        # set multiple times on export
        self.blocksize = None
        self.scaleDown = None

        # set on first access, see properties below
        self._isOldMwmbuilder = None
        self._fbximporter = None
        self._havokfilter = None
        self._mwmbuilder = None

    @property
    def isOldMwmbuilder(self):
        if self._isOldMwmbuilder == None:
            self._isOldMwmbuilder = (OLD_MWMBUILDER_MD5 == md5sum(self.mwmbuilder))
        return self._isOldMwmbuilder

    @property
    def fbximporter(self):
        if self._fbximporter == None:
            self._fbximporter = tool_path('havokFbxImporter', 'Havok FBX Importer')
        return self._fbximporter

    @property
    def mwmbuilder(self):
        if self._mwmbuilder == None:
            self._mwmbuilder = tool_path('mwmbuilder', 'mwmbuilder')
        return self._mwmbuilder

    @property
    def havokfilter(self):
        if self._havokfilter == None:
            self._havokfilter = tool_path('havokFilterMgr', 'Havok Filter Manager')
        return self._havokfilter

    def callTool(self, cmdline, logfile=None, cwd=None, successfulExitCodes=[0], loglines=[]):
        try:
            out = subprocess.check_output(cmdline, cwd=cwd, stderr=subprocess.STDOUT)
            if self.isLogToolOutput and logfile:
                write_to_log(logfile, out, cmdline=cmdline, cwd=cwd, loglines=loglines)

        except subprocess.CalledProcessError as e:
            if self.isLogToolOutput and logfile:
                write_to_log(logfile, e.output, cmdline=cmdline, cwd=cwd, loglines=loglines)
            if not e.returncode in successfulExitCodes:
                raise

    def template(self, templateString, **kwargs):
        keyValues = vars(self).copy()
        keyValues.update(kwargs)
        return Template(templateString).safe_substitute(**keyValues)

FWD = 'Z'
UP = 'Y'
MATRIX_NORMAL = axis_conversion(to_forward=FWD, to_up=UP).to_4x4()
MATRIX_SCALE_DOWN = scaleUni(0.2) * MATRIX_NORMAL

def export_fbx(settings: ExportSettings, filepath, objects):
    return save_single(
        settings.operator,
        settings.scene,
        filepath=filepath,
        context_objects=objects,
        object_types={'MESH', 'EMPTY'},
        global_matrix=MATRIX_SCALE_DOWN if settings.scaleDown else MATRIX_NORMAL,
        axis_forward=FWD,
        axis_up=UP,
        bake_space_transform=True,
        use_mesh_modifiers=True,
        mesh_smooth_type='OFF',
    )

def fbx_to_hkt(settings: ExportSettings, srcfile, dstfile):
    settings.callTool(
        [settings.fbximporter, srcfile, dstfile],
        logfile=dstfile+'.convert.log'
    )

from .havok_options import HAVOK_OPTION_FILE_CONTENT

def hkt_filter(settings: ExportSettings, srcfile, dstfile, options=HAVOK_OPTION_FILE_CONTENT):
    hko = tempfile.NamedTemporaryFile(mode='wt', prefix='space_engineers_', suffix=".hko", delete=False)
    try:
        with hko.file as f:
            f.write(options)

        settings.callTool(
            [settings.havokfilter, '-t', '-s', hko.name, '-p', dstfile, srcfile],
            logfile=dstfile+'.filter.log',
            successfulExitCodes=[0,1])
    finally:
        os.remove(hko.name)

def write_pretty_xml(etree_root_element, filepath):
    # what a hack...
    import xml.dom.minidom as dom
    xmlString = ElementTree.tostring(etree_root_element, 'utf-8')
    minidom = dom.parseString(xmlString)
    prettyXml = minidom.toprettyxml(encoding='utf-8')
    with open(filepath, mode='wb') as file:
        file.write(prettyXml)

def mwmbuilder(settings: ExportSettings, srcfile, dstfile):
    if not settings.isRunMwmbuilder:
        if settings.isLogToolOutput:
            write_to_log(dstfile+'.log', b"mwmbuilder skipped.")
        return

    settings.callTool(
        [settings.mwmbuilder, '/m:'+os.path.basename(srcfile)], #, '/o:'+os.path.dirname(dstfile)]
        cwd=os.path.dirname(srcfile),
        logfile=dstfile+'.log'
    )

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

    def export(self, settings: ExportSettings, havokFile=None):
        basepath = join(settings.outputDir, settings.template(self.filename_template))

        fbxfile = basepath + '.fbx'
        paramsfile = basepath + '.xml'
        havokfile = basepath + '.hkt'
        mwmfile = basepath + '.mwm'

        if havokFile:
            if havokFile != havokfile:
                shutil.copy2(havokFile, havokfile)

        paramsxml = mwmbuilder_xml(settings, (material_xml(settings, mat) for mat in self.materials))
        write_pretty_xml(paramsxml, paramsfile)
        if (settings.isOldMwmbuilder):
            write_to_log(paramsfile + '.log', b"Old version of mwmbuilder detected. Using different RescaleFactor.")

        export_fbx(settings, fbxfile, self.objects)

        mwmbuilder(settings, fbxfile, mwmfile)

        return mwmfile

class HavokSet(ExportSet):
    def test(self, ob):
        return super().test(ob) and ob.rigid_body

    def export(self, settings: ExportSettings):
        basepath = join(settings.outputDir, settings.template(self.filename_template))
        havokfile = basepath + '.hkt'

        if settings.isLogToolOutput and len(self.objects) == 0:
            write_to_log(havokfile + '.convert.log', b"no collision available for export")
            return None

        fbxfile = basepath + '.fbx'
        export_fbx(settings, fbxfile, self.objects)

        fbx_to_hkt(settings, fbxfile, havokfile)
        hkt_filter(settings, havokfile, havokfile)

        return havokfile

class MountPointSet(ExportSet):
    def export(self, settings: ExportSettings, modelFile, constrModelFiles):
        d = data(settings.scene)

        block = ElementTree.Element('Definition')

        id = ElementTree.SubElement(block, 'Id')
        subtypeId = ElementTree.SubElement(id, 'SubtypeId')
        subtypeId.text = settings.template(Names.subtypeid)

        icon = ElementTree.SubElement(block, 'Icon')
        icon.text = settings.template(Names.icon)

        ElementTree.SubElement(block, 'CubeSize').text = settings.blocksize
        ElementTree.SubElement(block, 'BlockTopology').text = 'TriangleMesh'

        x, z, y = d.block_dimensions # z and y switched on purpose; y is up, z is forward in SE
        ElementTree.SubElement(block, 'Size', x=str(x), y=str(y), z=str(z))

        ElementTree.SubElement(block, 'ModelOffset', x='0', y='0', z='0')

        modelpath = settings.template(Names.modelpath, modelfile=os.path.basename(modelFile))
        ElementTree.SubElement(block, 'Model').text = modelpath

        numConstr = len(constrModelFiles)
        if numConstr > 0:
            constr = ElementTree.SubElement(block, 'BuildProgressModels')
            for i, constrModelFile in enumerate(constrModelFiles):
                upperBound = "%.2f" % (1.0 * (i+1) / numConstr)
                constrModelpath = settings.template(Names.modelpath, modelfile=os.path.basename(constrModelFile))
                ElementTree.SubElement(constr, 'Model', BuildPercentUpperBound=upperBound, File=constrModelpath)

        mountpoints = mount_point_definitions(self.objects)
        if len(mountpoints) > 0:
            block.append(mount_points_xml(mountpoints))

        blockPairName = ElementTree.SubElement(block, 'BlockPairName')
        blockPairName.text = settings.template(Names.blockpairname)

        blockdeffile = join(settings.outputDir, settings.template(Names.blockpairname) + '.blockdef.xml')
        write_pretty_xml(block, blockdeffile)

        return blockdeffile

# mapping (scene.block_size) -> (block_size_name, apply_scale_down)
SIZES = {
    'LARGE' : [('Large', False)],
    'SMALL' : [('Small', False)],
    'SCALE_DOWN' : [('Large', False), ('Small', True)]
}

class BlockExport:
    def __init__(self, settings: ExportSettings):
        self.settings = settings

    def collectObjects(self):
        d = data(self.settings.scene)

        self.havok = HavokSet(layer_bits(d.physics_layers), Names.main)
        self.mp = MountPointSet(layer_bits(d.mount_points_layers), Names.main)

        self.main = MwmSet(layer_bits(d.main_layers), Names.main)

        constr_bits = [layer_bit(i) for i, layer in enumerate(d.construction_layers) if layer]
        self.constr = [MwmSet( bits, Template(Names.construction).safe_substitute(n = i+1))
            for i, bits in enumerate(constr_bits)]

        sets = [self.havok, self.mp, self.main] + self.constr

        for ob in self.settings.scene.objects:
            for set in sets:
                if set.test(ob):
                    set.collect(ob)

    def exportFiles(self):
        settings = self.settings
        d = data(settings.scene)

        for settings.blocksize, settings.scaleDown in SIZES[d.block_size]:
            havokfile = self.havok.export(settings)
            modelFile = self.main.export(settings, havokfile)
            constrFiles = [
                c.export(settings, havokfile)
                    for c in self.constr
            ]
            blockdeffile = self.mp.export(settings, modelFile, constrFiles)

    def export(self):
        self.collectObjects()
        self.exportFiles()

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
        col.prop(self, "rescale_factor")

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
                    settings = ExportSettings(scene, self.directory)
                    settings.operator = self
                    settings.isRunMwmbuilder = not self.skip_mwmbuilder

                    BlockExport(settings).export()

                    wm.progress_update(i)
            finally:
                 wm.progress_end()

        except FileNotFoundError as e: # raised when the addon preferences are missing some tool paths
            self.report({'ERROR'}, "Configuration error: %s" % e)

        except subprocess.CalledProcessError as e:
            self.report({'ERROR'}, "An external tool failed, check generated logs: %s" % e)

        return {'FINISHED'}
