from collections import namedtuple, OrderedDict
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
from space_engineers.merge_xml import CubeBlocksMerger, MergeResult
from space_engineers.mount_points import mount_point_definitions, mount_points_xml
from space_engineers.types import SESceneProperties
from .utils import scaleUni, layer_bits, layer_bit, md5sum
from .types import data, prefs, getExportNodeTreeFromContext
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

def pretty_xml(elem: ElementTree.Element, level=0, indent="\t"):
    i = "\n" + level*indent
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + indent
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            pretty_xml(elem, level+1, indent)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

def write_pretty_xml(elem: ElementTree.Element, filepath: str):
    pretty_xml(elem, indent="\t")
    ElementTree.ElementTree(elem).write(
        filepath, encoding="utf-8", xml_declaration=False, method="ordered-attribs")

class Names:
    subtypeid = '${blockname}_${blocksize}'
    blockpairname = '${blockname}'
    main = '${blockname}_${blocksize}'
    construction = '${blockname}_${blocksize}_Construction${n}'
    icon = 'Textures\Icons\${blockname}.dds'
    modelpath = '${modeldir}${modelfile}'

class ExportSettings:
    def __init__(self, scene, outputDir):
        def typeCast(data) -> SESceneProperties: # allows type inference in IDE
            return data

        self.scene = scene
        self.sceneData = typeCast(data(scene))
        self.outputDir = os.path.normpath(bpy.path.abspath(outputDir))
        self.blockname = scene.name # legacy, same as BlockPairName
        self.BlockPairName = scene.name # consistent with CubeBlocks.sbc
        self.operator = STDOUT_OPERATOR
        self.isLogToolOutput = True
        self.isRunMwmbuilder = True
        self.isFixDirBug = prefs().fix_dir_bug
        self.modeldir = 'Models\\'
        self.names = Names()

        # set multiple times on export
        self.blocksize = None # legacy, same as CubeSize
        self.CubeSize = None # consistent with CubeBlocks.sbc
        self.SubtypeId = None # consistent with CubeBlocks.sbc
        self.scaleDown = None
        self.isUseTangentSpace = False

        # set on first access, see properties below
        self._isOldMwmbuilder = None
        self._fbximporter = None
        self._havokfilter = None
        self._mwmbuilder = None

        self.cache = {}

    @property
    def isOldMwmbuilder(self):
        if self._isOldMwmbuilder is None:
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

    def msg(self, level, msg, file=None):
        if not file is None:
            msg = basename(file) +': '+ msg
        self.operator.report({level}, msg)

    def warn(self, msg, file=None):
        self.msg('WARNING', msg, file)

    def error(self, msg, file=None):
        self.msg('ERROR', msg, file)

    def info(self, msg, file=None):
        self.msg('INFO', msg, file)

    def cacheValue(self, key, value):
        self.cache[key] = value
        return value

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
        use_tspace=settings.isUseTangentSpace,
        bake_anim=False,
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

def mwmbuilder(settings: ExportSettings, srcfile: string, dstfile):
    if not settings.isRunMwmbuilder:
        if settings.isLogToolOutput:
            write_to_log(dstfile+'.log', b"mwmbuilder skipped.")
        return

    cmdline = [settings.mwmbuilder, '/m:'+os.path.basename(srcfile)]

    if settings.isFixDirBug:
        # the bug cuts the first 6 characters from the source directory
        # this prepends them again
        fix = "/o:" + os.path.dirname(srcfile)[:6]
        cmdline.append(fix)

    settings.callTool(cmdline, cwd=os.path.dirname(srcfile), logfile=dstfile+'.log')

class ExportSet:
    def __init__(self, layer_mask_bits, filename):
        self.layer_mask_bits = layer_mask_bits
        self.filename_template = filename
        self.objects = []

    def test(self, ob):
        return (layer_bits(ob.layers) & self.layer_mask_bits) != 0

    def collect(self, ob):
        self.objects.append(ob)

    def filenames(self, settings: ExportSettings):
        self.basepath = join(settings.outputDir, settings.template(self.filename_template))

class MwmSet(ExportSet):
    def __init__(self, layer_mask_bits, filename):
        super().__init__(layer_mask_bits, filename)
        self.materials = set()

    def collect(self, ob):
        super().collect(ob)
        self.materials |= {slot.material for slot in ob.material_slots if slot.material}

    def filenames(self, settings: ExportSettings):
        super().filenames(settings)
        self.fbxfile = self.basepath + '.fbx'
        self.paramsfile = self.basepath + '.xml'
        self.havokfile = self.basepath + '.hkt'
        self.mwmfile = self.basepath + '.mwm'

    def export(self, settings: ExportSettings, havokFile: string = None):
        self.filenames(settings)

        if havokFile:
            if havokFile != self.havokfile:
                shutil.copy2(havokFile, self.havokfile)

        paramsxml = mwmbuilder_xml(settings, (material_xml(settings, mat) for mat in self.materials))
        write_pretty_xml(paramsxml, self.paramsfile)

        if (settings.isOldMwmbuilder):
            write_to_log(self.paramsfile + '.log', b"Old version of mwmbuilder detected. Using different RescaleFactor.")

        export_fbx(settings, self.fbxfile, self.objects)

        mwmbuilder(settings, self.fbxfile, self.mwmfile)

        return self.mwmfile

class HavokSet(ExportSet):
    def test(self, ob):
        return super().test(ob) and ob.rigid_body

    def filenames(self, settings: ExportSettings):
        super().filenames(settings)
        self.havokfile = self.basepath + '.hkt'

    def export(self, settings: ExportSettings):
        self.filenames(settings)

        if settings.isLogToolOutput and len(self.objects) == 0:
            write_to_log(self.havokfile + '.convert.log', b"no collision available for export")
            return None

        fbxfile = self.havokfile + '.fbx'
        export_fbx(settings, fbxfile, self.objects)

        fbx_to_hkt(settings, fbxfile, self.havokfile)
        hkt_filter(settings, self.havokfile, self.havokfile)

        return self.havokfile

class MountPointSet(ExportSet):
    def generateXml(self, settings: ExportSettings, modelFile: string, constrModelFiles: string):
        d = data(settings.scene)

        block = ElementTree.Element('Definition')

        id = ElementTree.SubElement(block, 'Id')
        subtypeId = ElementTree.SubElement(id, 'SubtypeId')

        if d.use_custom_subtypeids:
            if settings.blocksize == 'Large' and d.large_subtypeid:
                subtypeId.text = d.large_subtypeid
            elif settings.blocksize == 'Small' and d.small_subtypeid:
                subtypeId.text = d.small_subtypeid

        if not subtypeId.text:
            subtypeId.text = settings.template(settings.names.subtypeid)

        icon = ElementTree.SubElement(block, 'Icon')
        icon.text = settings.template(settings.names.icon)

        ElementTree.SubElement(block, 'CubeSize').text = settings.blocksize
        ElementTree.SubElement(block, 'BlockTopology').text = 'TriangleMesh'

        x, z, y = d.block_dimensions # z and y switched on purpose; y is up, z is forward in SE
        eSize = ElementTree.SubElement(block, 'Size')
        eSize.attrib = OrderedDict([('x', str(x)), ('y', str(y)), ('z', str(z)), ])

        eOffset = ElementTree.SubElement(block, 'ModelOffset')
        eOffset.attrib = OrderedDict([('x', '0'), ('y', '0'), ('z', '0'), ])

        modelpath = settings.template(settings.names.modelpath, modelfile=os.path.basename(modelFile))
        ElementTree.SubElement(block, 'Model').text = modelpath

        numConstr = len(constrModelFiles)
        if numConstr > 0:
            constr = ElementTree.SubElement(block, 'BuildProgressModels')
            for i, constrModelFile in enumerate(constrModelFiles):
                upperBound = "%.2f" % (1.0 * (i+1) / numConstr)
                constrModelpath = settings.template(settings.names.modelpath, modelfile=os.path.basename(constrModelFile))
                eModel = ElementTree.SubElement(constr, 'Model')
                eModel.attrib = OrderedDict([('BuildPercentUpperBound', upperBound), ('File', constrModelpath), ])

        mountpoints = mount_point_definitions(self.objects)
        if len(mountpoints) > 0:
            block.append(mount_points_xml(mountpoints))

        blockPairName = ElementTree.SubElement(block, 'BlockPairName')
        blockPairName.text = settings.template(settings.names.blockpairname)

        return block

    def filenames(self, settings: ExportSettings):
        super().filenames(settings)
        self.blockdeffile = self.basepath + '.blockdef.xml'

    def export(self, settings: ExportSettings, modelFile: string, constrModelFiles: string):
        block = self.generateXml(settings, modelFile, constrModelFiles)

        self.filenames(settings)

        write_pretty_xml(block, self.blockdeffile)

        return self.blockdeffile

# mapping (scene.block_size) -> (block_size_name, apply_scale_down)
SIZES = {
    'LARGE' : [('Large', False)],
    'SMALL' : [('Small', False)],
    'SCALE_DOWN' : [('Large', False), ('Small', True)]
}

class BlockExport:
    def __init__(self, settings: ExportSettings):
        self.settings = settings

        d = data(self.settings.scene)

        self.havok = HavokSet(layer_bits(d.physics_layers), settings.names.main)
        self.mp = MountPointSet(layer_bits(d.mount_points_layers), settings.names.main)

        self.main = MwmSet(layer_bits(d.main_layers), settings.names.main)

        constr_bits = [layer_bit(i) for i, layer in enumerate(d.construction_layers) if layer]
        self.constr = [MwmSet( bits, Template(settings.names.construction).safe_substitute(n = i+1))
                       for i, bits in enumerate(constr_bits)]

        self.sets = [self.havok, self.mp, self.main] + self.constr

    def collectObjects(self):
        for ob in self.settings.scene.objects:
            for set in self.sets:
                if set.test(ob):
                    set.collect(ob)

    def mergeBlockDefs(self, cubeBlocks: CubeBlocksMerger):
        self.collectObjects()

        def mwmfile(artifact: MwmSet, settings: ExportSettings):
            artifact.filenames(settings)
            return artifact.mwmfile

        settings = self.settings
        failed = False

        for settings.blocksize, settings.scaleDown in SIZES[settings.sceneData.block_size]:
            xml = self.mp.generateXml(
                settings,
                mwmfile(self.main, settings),
                [mwmfile(c, settings) for c in self.constr],
            )

            result = cubeBlocks.merge(xml)
            if MergeResult.NOT_FOUND in result:
                failed = True
                settings.operator.report({'WARNING'}, "CubeBlocks.sbc contained no definition for SubtypeId [%s]" % (
                    xml.findtext("./Id/SubtypeId")))
            elif MergeResult.MERGED in result:
                settings.operator.report({'INFO'}, "Updated SubtypeId [%s]" % (xml.findtext("./Id/SubtypeId")))

        return not failed

    def exportFiles(self):
        settings = self.settings

        for settings.blocksize, settings.scaleDown in SIZES[settings.sceneData.block_size]:
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
    use_tspace = bpy.props.BoolProperty(
        name="Tangent Space",
        description="Add binormal and tangent vectors, together with normal they form the tangent space "
                    "(will only work correctly with tris/quads only meshes!)",
        default=False)

    settings_name = bpy.props.StringProperty(
        name="Used Settings",
        description="The name of the node-tree that defines the export",
        default="")

    @classmethod
    def poll(self, context):
        if not context.scene:
            return False

        d = data(context.scene)
        if d is None or not d.is_block:
            return False

        tree = getExportNodeTreeFromContext(context)
        return not tree is None

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
        col.prop(self, "use_tspace")

    def execute(self, context):
        org_mode = None

        try:
            if context.active_object and context.active_object.mode != 'OBJECT' and bpy.ops.object.mode_set.poll():
                org_mode = context.active_object.mode
                bpy.ops.object.mode_set(mode='OBJECT')

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
                    settings.isUseTangentSpace = self.use_tspace

                    BlockExport(settings).export()

                    wm.progress_update(i)
            finally:
                wm.progress_end()

        except FileNotFoundError as e: # raised when the addon preferences are missing some tool paths
            self.report({'ERROR'}, "Configuration error: %s" % e)

        except subprocess.CalledProcessError as e:
            self.report({'ERROR'}, "An external tool failed, check generated logs: %s" % e)

        finally:
            if context.active_object and org_mode and bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode=org_mode)

        return {'FINISHED'}

class UpdateDefinitionsFromBlockScene(bpy.types.Operator):
    bl_idname = "export_scene.space_engineers_update_definitions"
    bl_label = "Update Block Definitions"
    bl_description = "Update the block-definitions in CubeBlocks.sbc."

    filepath = bpy.props.StringProperty(subtype='FILE_PATH')

    all_scenes = bpy.props.BoolProperty(
        name="All Scenes",
        description="Update with data from all scenes that are marked as blocks.")
    create_backup = bpy.props.BoolProperty(
        name="Backup Target File",
        description="Creates a backup of the target file before updating.")
    allow_renames = bpy.props.BoolProperty(
        name="Update SubtypeIds",
        description="Renames the SubtypeId if a definition matches by BlockPairName and CubeSize. "
                    "Be aware that this is not backwards-compatible for players!")

    settings_name = bpy.props.StringProperty(
        name="Used Settings",
        description="The name of the node-tree that defines the export",
        default="MwmExport")

    @classmethod
    def poll(self, context):
        if not context.scene:
            return False

        d = data(context.scene)
        if d is None or not d.is_block:
            return False

        tree = getExportNodeTreeFromContext(context)
        if tree is None or not any(n for n in tree.nodes if n.bl_idname == "SEBlockDefNode"):
            return False

        return True

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = os.path.dirname(context.blend_data.filepath)

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        lay = self.layout

        col = lay.column()
        col.prop(self, "all_scenes")
        col.prop(self, "create_backup")

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        dir = os.path.dirname(path)
        merger = CubeBlocksMerger(cubeBlocksPath=path, backup=self.create_backup)

        if self.all_scenes:
            scenes = [scene for scene in bpy.data.scenes if data(scene).is_block]
        else:
            scenes = [context.scene]

        wm = context.window_manager
        wm.progress_begin(0, len(scenes))
        try:
            for i, scene in enumerate(scenes):
                settings = ExportSettings(scene, dir)
                settings.operator = self

                BlockExport(settings).mergeBlockDefs(merger)

                wm.progress_update(i)

            merger.write()
        finally:
            wm.progress_end()

        return {'FINISHED'}
