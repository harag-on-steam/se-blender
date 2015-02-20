import os
import re
import string
import subprocess
import tempfile
import bpy
from collections import OrderedDict
from os.path import basename
from string import Template
from xml.etree import ElementTree

from .mount_points import mount_point_definitions, mount_points_xml
from .utils import scaleUni, md5sum
from .types import data, prefs, SESceneProperties
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
    subtypeid = '${BlockPairName}_${CubeSize}'
    main = '${SubtypeId}'
    construction = '${SubtypeId}_Constr${n}'
    lod = '${SubtypeId}_LOD${n}'
    icon = '${IconsDir}${BlockPairName}.dds'
    modelpath = '${ModelsDir}${modelfile}'

_RE_BLOCK_NAME = re.compile(r"^(.+)\.(Large|Small|\d+)$", re.IGNORECASE)

def func():
    pass
_FUNCTION_TYPE = type(func)
del func

class ExportSettings:
    def __init__(self, scene, outputDir, exportNodes=None):
        def typeCast(data) -> SESceneProperties: # allows type inference in IDE
            return data

        self.scene = scene # ObjectSource.getObjects() uses .utils.scene() instead
        self.sceneData = typeCast(data(scene))
        self.exportNodes = bpy.data.node_groups[self.sceneData.export_nodes] if exportNodes is None else exportNodes
        self.outputDir = os.path.normpath(bpy.path.abspath(outputDir))
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

        # substitution parameters
        # self.BlockPairName # corresponds with element-name in CubeBlocks.sbc, see property below
        self.ModelsDir = 'Models\\'
        self.IconsDir = 'Textures\\Icons\\'
        # set multiple times on export
        self._CubeSize = None # corresponds with element-name in CubeBlocks.sbc, setter also sets SubtypeId
        self.SubtypeId = None # corresponds with element-name in CubeBlocks.sbc

        self.cache = {}

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

    def info(self, msg, file=None, node = None):
        self.msg('INFO', msg, file, node)

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

def generateBlockDefXml(settings: ExportSettings, modelFile: string, mountPointObjects: iter, constrModelFiles: string):
    d = data(settings.scene)

    block = ElementTree.Element('Definition')

    id = ElementTree.SubElement(block, 'Id')
    subtypeId = ElementTree.SubElement(id, 'SubtypeId')
    subtypeId.text = settings.SubtypeId

    icon = ElementTree.SubElement(block, 'Icon')
    icon.text = settings.template(settings.names.icon)

    ElementTree.SubElement(block, 'CubeSize').text = settings.CubeSize
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

    mountpoints = mount_point_definitions(mountPointObjects)
    if len(mountpoints) > 0:
        block.append(mount_points_xml(mountpoints))

    blockPairName = ElementTree.SubElement(block, 'BlockPairName')
    blockPairName.text = settings.BlockPairName

    return block


