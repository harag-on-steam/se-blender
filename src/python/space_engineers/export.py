import os
import re
import subprocess
import tempfile
import bpy
from collections import OrderedDict
from os.path import basename, join
from string import Template
from xml.etree import ElementTree
import shutil
from mathutils import Matrix

from .mount_points import mount_point_definitions, mount_points_xml
from .mirroring import mirroringAxisFromObjectName
from .utils import scaleUni, md5sum
from .types import data, prefs, getBaseDir, SESceneProperties
from .fbx import save_single

from bpy_extras.io_utils import axis_conversion, ExportHelper

class StdoutOperator():
    def report(self, type, message):
        print(message)

STDOUT_OPERATOR = StdoutOperator()

# mwmbuilder from Space Engineers 01.051
OLD_MWMBUILDER_MD5 = '261163f6d3743d28fede7944b2b0949a'

class MissbehavingToolError(subprocess.SubprocessError):
    def __init__(self, message: str):
        self.message = message

    def __str__(self):
        return self.message

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
    subtypeid = '${BlockPairName}_${CubeSize}'
    main = '${SubtypeId}'
    construction = '${SubtypeId}_Constr${n}'
    lod = '${SubtypeId}_LOD${n}'
    icon = '${IconsDir}\\${iconfile}'
    modelpath = '${ModelsDir}${modelfile}'

_RE_BLOCK_NAME = re.compile(r"^(.+)\.(Large|Small|\d+)$", re.IGNORECASE)

def func():
    pass
_FUNCTION_TYPE = type(func)
del func

class ExportSettings:
    def __init__(self, scene, outputDir=None, exportNodes=None, mwmDir=None):
        def typeCast(data) -> SESceneProperties: # allows type inference in IDE
            return data

        self.scene = scene # ObjectSource.getObjects() uses .utils.scene() instead
        self.sceneData = typeCast(data(scene))
        self.outputDir = os.path.normpath(bpy.path.abspath(self.sceneData.export_path if outputDir is None else outputDir))
        self.exportNodes = bpy.data.node_groups[self.sceneData.export_nodes] if exportNodes is None else exportNodes
        self.baseDir = getBaseDir(scene)
        # temporary working directory. used as a workaround for two bugs in mwmbuilder. must be empty initially.
        self.mwmDir = mwmDir if not mwmDir is None else self.outputDir
        self.operator = STDOUT_OPERATOR
        self.isLogToolOutput = True
        self.isRunMwmbuilder = True
        self.isFixDirBug = prefs().fix_dir_bug
        self.names = Names()
        self.isUseTangentSpace = False
        # set on first access, see properties below
        self._isOldMwmbuilder = None
        self._fbximporter = None
        self._havokfilter = None
        self._mwmbuilder = None
        # set multiple times on export
        self.scaleDown = None
        self._hadErrors = False

        # substitution parameters
        # self.BlockPairName # corresponds with element-name in CubeBlocks.sbc, see property below
        try:
            self.ModelsDir = os.path.relpath(self.outputDir, self.baseDir) + '\\'
        except (ValueError):
            self.ModelsDir = 'Models\\' # fall back to old behaviour if baseDir and outputDir are on different drives
        try:
            self.IconsDir = bpy.path.relpath('//Textures/Icons', self.baseDir)
        except (ValueError):
            self.IconsDir = '//Textures/Icons' # fall back to old behaviour if baseDir and outputDir are on different drives
        # set multiple times on export
        self._CubeSize = None # corresponds with element-name in CubeBlocks.sbc, setter also sets SubtypeId
        self.SubtypeId = None # corresponds with element-name in CubeBlocks.sbc

        self.cache = {}

    def mirrorSettings(self):
        mirrorSceneData = self.sceneData.getMirroringBlock()
        if mirrorSceneData is None:
            return None

        mirrorSettings = ExportSettings(mirrorSceneData.scene, self.outputDir)
        mirrorSettings.CubeSize = self.CubeSize
        mirrorSettings.scaleDown = self.scaleDown
        return mirrorSettings

    @property
    def CubeSize(self):
        return self._CubeSize

    @CubeSize.setter
    def CubeSize(self, value):
        self._CubeSize = value
        self.SubtypeId = self.template(self.names.subtypeid)
        d = self.sceneData
        if d and d.use_custom_subtypeids:
            if self.CubeSize == 'Large' and d.large_subtypeid:
                self.SubtypeId = d.large_subtypeid
            elif self.CubeSize == 'Small' and d.small_subtypeid:
                self.SubtypeId = d.small_subtypeid

    @property
    def BlockPairName(self): # the scene name without Blender's ".nnn" suffix
        name = self.scene.name
        m = _RE_BLOCK_NAME.search(name)
        return m.group(1) if m else name

    @property
    def blockname(self): # legacy, read-only
        return self.BlockPairName

    @property
    def blocksize(self): # legacy, read-only
        return self.CubeSize

    @property
    def isOldMwmbuilder(self):
        if self._isOldMwmbuilder is None:
            self._isOldMwmbuilder = False # (OLD_MWMBUILDER_MD5 == md5sum(self.mwmbuilder))
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

    @property
    def hadErrors(self):
        if self._hadErrors:
            self._hadErrors = False
            return True
        return False

    def callTool(self, cmdline, logfile=None, cwd=None, successfulExitCodes=[0], loglines=[], logtextInspector=None):
        try:
            out = subprocess.check_output(cmdline, cwd=cwd, stderr=subprocess.STDOUT)
            if self.isLogToolOutput and logfile:
                write_to_log(logfile, out, cmdline=cmdline, cwd=cwd, loglines=loglines)
            if not logtextInspector is None:
                logtextInspector(out)

        except subprocess.CalledProcessError as e:
            if self.isLogToolOutput and logfile:
                write_to_log(logfile, e.output, cmdline=cmdline, cwd=cwd, loglines=loglines)
            if not e.returncode in successfulExitCodes:
                raise

    def template(self, templateString, **kwargs):
        return Template(templateString).safe_substitute(self, **kwargs)

    def msg(self, level, msg, file=None, node = None):
        if not file is None and not node is None:
            msg = "%s (%s): %s" % (basename(file), node.name, msg)
        elif not file is None:
            msg = "%s: %s" % (basename(file), msg)
        elif not node is None:
            msg = "(%s): %s" % (node.name, msg)
        self.operator.report({level}, msg)

    def warn(self, msg, file=None, node = None):
        self.msg('WARNING', msg, file, node)

    def error(self, msg, file=None, node = None):
        self.msg('ERROR', msg, file, node)
        self._hadErrors = True

    def info(self, msg, file=None, node = None):
        self.msg('INFO', msg, file, node)

    def text(self, msg, file=None, node = None):
        self.msg('OPERATOR', msg, file, node)

    def cacheValue(self, key, value):
        self.cache[key] = value
        return value

    def __getitem__(self, key): # makes all attributes available for parameter substitution
        if not type(key) is str or key.startswith('_'):
            raise KeyError(key)
        try:
            value = getattr(self, key)
            if value is None or type(value) is _FUNCTION_TYPE:
                raise KeyError(key)
            return value
        except AttributeError:
            raise KeyError(key)

# FWD = 'Z'
# UP = 'Y'
# MATRIX_NORMAL = axis_conversion(to_forward=FWD, to_up=UP).to_4x4()
# MATRIX_SCALE_DOWN = Matrix.Scale(0.2, 4) * MATRIX_NORMAL

def export_fbx(settings: ExportSettings, filepath, objects, fbx_settings = None):

    fbxSettings = {
        # FBX operator defaults
        # some internals of the fbx exporter depend on them and will step out of line if they are not present
        'version': 'BIN7400',
        'use_mesh_edges': False,
        'use_custom_props': False, # SE / Havok properties are hacked directly into the modified fbx importer
        # anim, BIN7400
        'bake_anim': False, # no animation export to SE by default
        'bake_anim_use_all_bones': True,
        'bake_anim_use_nla_strips': False,
        'bake_anim_use_all_actions': False,
        'bake_anim_force_startend_keying': True,
        'bake_anim_step': 1.0,
        'bake_anim_simplify_factor': 1.0,
        # anim, ASCII6100
        'use_anim' : False, # no animation export to SE by default
        'use_anim_action_all' : True,
        'use_default_take' : True,
        'use_anim_optimize' : True,
        'anim_optimize_precision' : 6.0,
        # referenced files stay on automatic, MwmBuilder only cares about what's written to its .xml file
        'path_mode': 'AUTO',
        'embed_textures': False,
        # batching isn't used because the export is driven by the node tree
        'batch_mode': 'OFF',
        'use_batch_own_dir': True,
        'use_metadata': True,
        # important settings for SE
        'object_types': {'MESH', 'EMPTY'},
        'axis_forward': 'Z',
        'axis_up': 'Y',
        'bake_space_transform': True, # the export to Havok needs this, it's off for the MwmFileNode
        'use_mesh_modifiers': True,
        'mesh_smooth_type': 'OFF',
        'use_tspace': settings.isUseTangentSpace, # TODO deprecate settings.isUseTangentSpace
        # for characters
        'global_scale': 1.0,
        'use_armature_deform_only': False,
        'add_leaf_bones': False,
        'armature_nodetype': 'NULL',
        'primary_bone_axis': 'X',
        'secondary_bone_axis': 'Y',
    }

    if fbx_settings:
        if isinstance(fbx_settings, bpy.types.PropertyGroup):
            fbx_settings = {p : getattr(fbx_settings, p) for p in fbx_settings.rna_type.properties.keys()}
        fbxSettings.update(**fbx_settings)

    # these cannot be overriden and are always set here
    fbxSettings['use_selection'] = False # because of context_objects
    fbxSettings['context_objects'] = objects

    global_matrix = axis_conversion(to_forward=fbxSettings['axis_forward'], to_up=fbxSettings['axis_up']).to_4x4()
    scale = fbxSettings['global_scale']
    if (settings.scaleDown):
        scale *= 0.2
    if abs(1.0-scale) >= 0.0001:
        global_matrix = Matrix.Scale(scale, 4) * global_matrix
    fbxSettings['global_matrix'] = global_matrix

    return save_single(
        settings.operator,
        settings.scene,
        filepath=filepath,
        **fbxSettings
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

def mwmbuilder(settings: ExportSettings, fbxfile: str, havokfile: str, paramsfile: str, mwmfile: str):
    if not settings.isRunMwmbuilder:
        if settings.isLogToolOutput:
            write_to_log(mwmfile+'.log', b"mwmbuilder skipped.")
        return

    contentDir = join(settings.mwmDir, 'Content')
    os.makedirs(contentDir, exist_ok = True)
    basename = os.path.splitext(os.path.basename(mwmfile))[0]

    def copy(srcfile: str, dstfile: str):
        if not srcfile is None and dstfile != srcfile:
            shutil.copy2(srcfile, dstfile)

    copy(fbxfile, join(contentDir, basename + '.fbx'))
    copy(paramsfile, join(contentDir, basename + '.xml'))
    copy(havokfile, join(contentDir, basename + '.hkt'))

    cmdline = [settings.mwmbuilder, '/s:Content', '/m:'+basename+'.fbx', '/o:.\\']

    def checkForLoggedErrors(logtext):
        if b": ERROR:" in logtext:
            raise MissbehavingToolError('MwmBuilder failed without an appropriate exit-code. Please check the log-file.')

    settings.callTool(cmdline, cwd=settings.mwmDir, logfile=mwmfile+'.log', logtextInspector=checkForLoggedErrors)
    copy(join(settings.mwmDir, basename + '.mwm'), mwmfile)

def generateBlockDefXml(
        settings: ExportSettings,
        modelFile: str,
        iconFile: str,
        mountPointObjects: iter,
        mirroringObjects: iter,
        mirroringBlockSubtypeId: str,
        constrModelFiles: iter):

    d = data(settings.scene)

    block = ElementTree.Element('Definition')

    id = ElementTree.SubElement(block, 'Id')
    subtypeId = ElementTree.SubElement(id, 'SubtypeId')
    subtypeId.text = settings.SubtypeId

    if iconFile: # only change the icon if there's actually an iconFile
        icon = ElementTree.SubElement(block, 'Icon')
        if not os.path.splitext(iconFile)[1]:
            iconFile += ".dds"
        try:
            icon.text = os.path.relpath(os.path.join(settings.baseDir, bpy.path.abspath(iconFile)), settings.baseDir)
        except ValueError:
            icon.text = settings.template(settings.names.icon, iconfile=iconFile)

    ElementTree.SubElement(block, 'CubeSize').text = settings.CubeSize
    ElementTree.SubElement(block, 'BlockTopology').text = 'TriangleMesh'

    x, z, y = d.block_dimensions # z and y switched on purpose; y is up, z is forward in SE
    eSize = ElementTree.SubElement(block, 'Size')
    eSize.attrib = OrderedDict([('x', str(x)), ('y', str(y)), ('z', str(z)), ])

    eOffset = ElementTree.SubElement(block, 'ModelOffset')
    eOffset.attrib = OrderedDict([('x', '0'), ('y', '0'), ('z', '0'), ])

    try:
        modelpath = os.path.relpath(os.path.join(settings.outputDir, modelFile), settings.baseDir)
    except ValueError:
        modelpath = settings.template(settings.names.modelpath, modelfile=modelFile)
    ElementTree.SubElement(block, 'Model').text = modelpath

    numConstr = len(constrModelFiles)
    if numConstr > 0:
        constr = ElementTree.SubElement(block, 'BuildProgressModels')
        for i, constrModelFile in enumerate(constrModelFiles):
            upperBound = "%.2f" % (1.0 * (i+1) / numConstr)
            try:
                constrModelpath = os.path.relpath(os.path.join(settings.outputDir, constrModelFile), settings.baseDir)
            except ValueError:
                constrModelpath = settings.template(settings.names.modelpath, modelfile=constrModelFile)
            eModel = ElementTree.SubElement(constr, 'Model')
            eModel.attrib = OrderedDict([('BuildPercentUpperBound', upperBound), ('File', constrModelpath), ])

    mountpoints = mount_point_definitions(mountPointObjects)
    if len(mountpoints) > 0:
        block.append(mount_points_xml(mountpoints))

    if mirroringBlockSubtypeId is not None:
        mirroringBlock = ElementTree.SubElement(block, "MirroringBlock")
        mirroringBlock.text = mirroringBlockSubtypeId

    mirroring = {}
    for o in mirroringObjects:
        axis = mirroringAxisFromObjectName(o)
        if not mirroring.get(axis, None):
            enum = o.space_engineers_mirroring
            if not enum in {'Unsupported', 'NonRectangular'}:
                mirroring[axis] = enum
            else:
                settings.warn("Mirroring%s defined by object %s is '%s'. Reset to 'None'." % (axis, o.name, enum))
    if len(mirroring) > 0:
        for axis in ('X','Y','Z'):
            mirroringElem = ElementTree.SubElement(block, 'Mirroring'+axis)
            mirroringElem.text = mirroring.get(axis, 'None')

    blockPairName = ElementTree.SubElement(block, 'BlockPairName')
    blockPairName.text = settings.BlockPairName

    return block


