from enum import Enum
import re
import bpy
from .texture_files import TextureType


class MathOperation(Enum):
    ADD = 1
    SUBTRACT = 2
    MULTIPLY = 3
    DIVIDE = 4
    SINE = 5
    COSINE = 6
    TANGENT = 7
    ARCSINE = 8
    ARCCOSINE = 9
    ARCTANGENT = 10
    POWER = 11
    LOGARITHM = 12
    MINIMUM = 13
    MAXIMUM = 14
    ROUND = 15
    LESS_THAN = 16
    GREATER_THAN = 17
    MODULO = 18
    ABSOLUTE = 19

class NormalSpace(Enum):
    TANGENT = 1
    OBJECT = 2
    WORLD = 3
    BLENDER_OBJECT = 4
    BLENDER_WORLD = 5

class GlossyDistribution(Enum):
    SHARP = 1
    BECKMANN = 2
    GGX = 3
    ASHIKHMIN_SHIRLEY = 4

class ImageColorspace(Enum):
    COLOR = 1
    NONE = 2

class CreateMode(Enum):
    ADD = 1
    REPLACE = 2
    REUSE = 3

def blId(nodeType):
    return nodeType.bl_rna.identifier

_RE_BLENDER_NAME = re.compile(r"^(.+?)(?:\.(\d+)+)?$")
_RE_WHITESPACE = re.compile(r"(?:\s|[-+/.])+")

def firstMatching(iterable, type, name=None):
    if not name is None:
        for item in iterable:
            if not isinstance(item, type):
                continue
            match = _RE_BLENDER_NAME.match(item.name)
            if match and match.group(1) == name:
                return item
    else:
        for item in iterable:
            if isinstance(item, type):
                return item
    return None

class SocketSpec:
    def __init__(self, type, name, default=None, min=None, max=None):
        self.type = type
        self.name = name
        self.default = default
        self.min = min
        self.max = max

class ShaderNodesBuilder:
    def __init__(self, tree: bpy.types.NodeTree, defaultCreate=CreateMode.ADD):
        self.tree = tree
        self.defaultCreate = defaultCreate

    def newNode(self, nodeType, name=None, label=None, location=None, width=None, create=None):
        if name is None and label:
            name = _RE_WHITESPACE.sub("", label)
        if create is None: create = self.defaultCreate

        node = None
        if create in {CreateMode.REPLACE, CreateMode.REUSE}:
            node = firstMatching(self.tree.nodes, nodeType, name)
            if not node is None and create == CreateMode.REPLACE:
                self.tree.nodes.remove(node)
                node = None
        if node is None:
            node = self.tree.nodes.new(blId(nodeType))

        if not label is None: node.label = label
        if not name is None: node.name = name
        if not location is None: node.location = location
        if not width is None: node.width = width

        return node

    def connectSockets(self, sourceTargetPairs):
        for source, target in sourceTargetPairs:
            self.connect(target, source)

    def connect(self, target, source):
        if not source is None:
            if isinstance(source, bpy.types.NodeSocket):
                self.tree.links.new(source, target)
            else:
                target.default_value = source
        elif isinstance(target, bpy.types.NodeSocket):
            for l in target.links:
                self.tree.links.remove(l)

    def newMath(self, name=None, label=None, location=None, op=None, clamp=None, op1=None, op2=None):
        n = self.newNode(bpy.types.ShaderNodeMath, name, label, location)
        if not op is None: n.operation = op.name
        if not clamp is None: n.use_clamp = clamp
        self.connectSockets(((op1, n.inputs[0]), (op2, n.inputs[1])))
        return n.outputs[0]

    def newGlossy(self, name=None, label=None, location=None, distribution=None, color=None, roughness=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeBsdfGlossy, name, label, location)
        if not distribution is None: n.distribution = distribution.name
        self.connectSockets(((color, n.inputs[0]), (roughness, n.inputs[1]), (normal, n.inputs[2])))
        return n.outputs[0]

    def newDiffuse(self, name=None, label=None, location=None, color=None, roughness=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeBsdfDiffuse, name, label, location)
        self.connectSockets(((color, n.inputs[0]), (roughness, n.inputs[1]), (normal, n.inputs[2])))
        return n.outputs[0]

    def newFresnel(self, name=None, label=None, location=None, ior=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeFresnel, name, label, location)
        self.connectSockets(((ior, n.inputs[0]), (normal, n.inputs[1])))
        return n.outputs[0]

    def newMix(self, name=None, label=None, location=None, factor=None, shader1=None, shader2=None):
        n = self.newNode(bpy.types.ShaderNodeMixShader, name, label, location)
        self.connectSockets(((factor, n.inputs[0]), (shader1, n.inputs[1]), (shader2, n.inputs[2])))
        return n.outputs[0]

    def newAdd(self, name=None, label=None, location=None, shader1=None, shader2=None):
        n = self.newNode(bpy.types.ShaderNodeAddShader, name, label, location)
        self.connectSockets(((shader1, n.inputs[0]), (shader2, n.inputs[1])))
        return n.outputs[0]

    def newNormalMap(self, name=None, label=None, location=None, space=None, uvMapName=None, strength=None, normal=None):
        n = self.newNode(bpy.types.ShaderNodeNormalMap, name, label, location)
        if not space is None: n.space = space.name
        if not uvMapName is None: n.uv_map = uvMapName
        self.connectSockets(((strength, n.inputs[0]), (normal, n.inputs[1])))
        return n.outputs[0]

    def newSeparateRgb(self, name=None, label=None, location=None, color=None):
        n = self.newNode(bpy.types.ShaderNodeSeparateRGB, name, label, location)
        self.connectSockets([(color, n.inputs[0])])
        return (n.outputs[0], n.outputs[1], n.outputs[2]) # rgb

    def newImageTexture(self, name=None, label=None, location=None, space=None, image=None):
        n = self.newNode(bpy.types.ShaderNodeTexImage, name, label, location)
        if not label is None: n.name = label
        if not space is None: n.color_space = space.name
        if not image is None: n.image = image
        return (n.outputs[0], n.outputs[1]) # color + alpha

    def newRgbValue(self, name=None, label=None, location=None, default=None):
        n = self.newNode(bpy.types.ShaderNodeRGB, name, label, location)
        if not default is None: n.outputs[0].default_value = default
        return n.outputs[0]

    def newFloatValue(self, name=None, label=None, location=None, default=None):
        n = self.newNode(bpy.types.ShaderNodeValue, name, label, location)
        if not default is None: n.outputs[0].default_value = default
        return n.outputs[0]

    def newTreeSockets(self, socketSpecs, output=False, location=None):
        if output:
            n = self.newNode(bpy.types.NodeGroupOutput, location=location, create=CreateMode.REUSE)
            nodeSockets = n.inputs
        else:
            n = self.newNode(bpy.types.NodeGroupInput, location=location, create=CreateMode.REUSE)
            nodeSockets = n.outputs

        # keep sockets until they do not match in type
        unmatched = min(len(socketSpecs), len(nodeSockets))
        for i, (socket, spec) in enumerate(zip(nodeSockets, socketSpecs)):
            if not isinstance(socket, spec.type):
                unmatched = i
                break

        # node-group sockets are added on the tree, not on the input/output nodes
        treeSockets = self.tree.outputs if output else self.tree.inputs

        # prune, beginning with the first non-matching socket and add missing sockets after that according to spec
        while (unmatched < len(treeSockets)):
            treeSockets.remove(treeSockets[len(treeSockets)-1])
        for spec in socketSpecs[unmatched:]:
            treeSockets.new(blId(spec.type), spec.name)

        # configure all sockets according to spec
        for socket, spec in zip(treeSockets, socketSpecs):
            socket.name = spec.name
            if not spec.default is None: socket.default_value = spec.default
            if not spec.min is None: socket.min_value = spec.min
            if not spec.max is None: socket.max_value = spec.max

        return [s for s in nodeSockets[0:len(socketSpecs)]]

    def newFloatInput(self, label, default=None, min=None, max=None):
        input = self.tree.inputs.new(blId(bpy.types.NodeSocketFloatUnsigned), label)
        if not default is None: input.default_value = default
        if not min is None: input.min_value = min
        if not max is None: input.max_value = max
        return input

    def newColorInput(self, label, default=(0.8, 0.8, 0.8, 1.0)): # default Blender near-white
        input = self.tree.inputs.new(blId(bpy.types.NodeSocketColor), label)
        if not default is None: input.default_value = default
        return input

DX11_NAME = 'SpaceEngineers_DX11_Shader_2'
DX9_NAME = 'SpaceEngineers_DX9_Shader'

def createDx11ShaderGroup():
    pbr = firstMatching(bpy.data.node_groups, bpy.types.ShaderNodeTree, DX11_NAME)
    if not pbr:
        pbr = bpy.data.node_groups.new(DX11_NAME, blId(bpy.types.ShaderNodeTree))
    builder = ShaderNodesBuilder(pbr, defaultCreate=CreateMode.REUSE)

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets of the node-group

    socketSpecs = [
        # ColorMetalTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Base Color Map", (0.8, 0.8, 0.8, 1.0)),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Metalness Map", 0.0, 0.0, 1.0),

        # NormalGlossTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Normal Map", (0.5, 0.5, 1.0, 1.0)), # straight up
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Normal Map Strength", 1.5, 0.0, 10.0),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Glossiness Map", 0.0, 0.0, 1.0),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "IOR", 1.450, 0.0, 1000.0),

        # AddMapsTexture
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Ambient Occlusion Map", 1.0, 0.0, 1.0),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Ambient Occlusion Power", 1.0, 0.0, 1.0),

        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Emissivity Map", 0.0, 0.0, 1.0),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Emissive Strength", 1.0, 0.0, 100.0),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Emissive Color Override", 0.0, 0.0, 1.0),
        SocketSpec(bpy.types.NodeSocketColor,         "Emissive Color", (0.0, 1.0, 0.0, 1.0)),

        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Coloring Mask", 0.0, 0.0, 1.0),
        SocketSpec(bpy.types.NodeSocketColor,         "Recolor", (0.8, 0.8, 0.8, 1.0)),
    ]
    builder.newTreeSockets(socketSpecs, False, (-400, 0))

    socketSpecs = [
        SocketSpec(bpy.types.NodeSocketShader, "Surface")
    ]
    builder.newTreeSockets(socketSpecs, True, (800, 0))

    # ------------------------------------------------------------------------------------------------------------#

    nodes = set()

    n0 = n = builder.newNode(bpy.types.NodeFrame, name='F.AO', label='Ambient Occlusion')
    nodes.add(n)
    n.location = (-777.102, 438.955)
    n.use_custom_color = True
    n.color = (0.787, 0.814, 0.510)
    n.width = 558.021

    n1 = n = builder.newNode(bpy.types.NodeGroupInput, name='Input.PBR', create=CreateMode.REUSE)
    nodes.add(n)
    n.location = (-897.568, -215.193)
    n.width = 194.717
    for i in [0, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13]:
        n.outputs[i].hide = True

    n2 = n = builder.newNode(bpy.types.ShaderNodeInvert, name='RoughnessInvert')
    nodes.add(n)
    n.location = (-487.574, -177.554)
    n.hide = 1.000
    builder.connect(n.inputs[0], 1.000)
    builder.connect(n.inputs[1], n1.outputs[4])
    for i in [0]:
        n.inputs[i].hide = True

    n3 = n = builder.newNode(bpy.types.NodeFrame, name='F.Recolor', label='Recoloring')
    nodes.add(n)
    n.location = (-975.192, -404.356)
    n.use_custom_color = True
    n.color = (0.774, 0.504, 0.485)
    n.width = 1010.506

    n4 = n = builder.newNode(bpy.types.NodeGroupInput, name='Input.Recolor', create=CreateMode.REUSE)
    nodes.add(n)
    n.parent = n3
    n.location = (-673.095, -10.912)
    n.width = 194.717
    for i in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]:
        n.outputs[i].hide = True

    n5 = n = builder.newNode(bpy.types.NodeReroute, name='RR.Recolor')
    nodes.add(n)
    n.parent = n3
    n.location = (-416.015, -34.621)
    n.width = 16.000
    builder.connect(n.inputs[0], n4.outputs[12])

    n6 = n = builder.newNode(bpy.types.ShaderNodeSeparateHSV, name='Recolor.Separate')
    nodes.add(n)
    n.parent = n3
    n.location = (-219.408, -77.022)
    n.hide = 1.000
    builder.connect(n.inputs[0], n4.outputs[13])

    n7 = n = builder.newNode(bpy.types.NodeReroute, name='RR.BaseColor')
    nodes.add(n)
    n.parent = n3
    n.location = (-400.269, -44.867)
    n.width = 16.000
    builder.connect(n.inputs[0], n4.outputs[0])

    n8 = n = builder.newNode(bpy.types.ShaderNodeRGBCurve, name='Recolor.Contrast')
    nodes.add(n)
    n.parent = n3
    n.location = (-324.110, -130.000)
    n.width = 210.843
    m = n.mapping
    m.curves[3].points.new(0.130, 0.380)
    m.update()
    builder.connect(n.inputs[0], 1.000)
    builder.connect(n.inputs[1], n7.outputs[0])

    n9 = n = builder.newNode(bpy.types.ShaderNodeMath, name='Recolor.Darken')
    nodes.add(n)
    n.parent = n3
    n.location = (-77.166, -109.416)
    n.hide = 1.000
    n.operation = 'MULTIPLY'
    n.use_clamp = True
    builder.connect(n.inputs[0], n6.outputs[2])
    builder.connect(n.inputs[1], n8.outputs[0])

    n10 = n = builder.newNode(bpy.types.ShaderNodeCombineHSV, name='Recolor.Combine')
    nodes.add(n)
    n.parent = n3
    n.location = (50.077, -77.380)
    n.hide = 1.000
    builder.connect(n.inputs[0], n6.outputs[0])
    builder.connect(n.inputs[1], n6.outputs[1])
    builder.connect(n.inputs[2], n9.outputs[0])

    n11 = n = builder.newNode(bpy.types.ShaderNodeMixRGB, name='RecolorMixColor')
    nodes.add(n)
    n.parent = n3
    n.location = (190.411, -36.504)
    n.hide = 1.000
    n.blend_type = 'MIX'
    n.use_clamp = True
    builder.connect(n.inputs[0], n5.outputs[0])
    builder.connect(n.inputs[1], n7.outputs[0])
    builder.connect(n.inputs[2], n10.outputs[0])

    n12 = n = builder.newNode(bpy.types.NodeGroupInput, name='Input.AO', create=CreateMode.REUSE)
    nodes.add(n)
    n.parent = n0
    n.location = (-157.050, -66.967)
    n.width = 188.236
    for i in [0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13]:
        n.outputs[i].hide = True

    n13 = n = builder.newNode(bpy.types.ShaderNodeMath, name='AOPower')
    nodes.add(n)
    n.parent = n0
    n.location = (80.425, -102.376)
    n.hide = 1.000
    n.operation = 'POWER'
    n.use_clamp = True
    builder.connect(n.inputs[0], n12.outputs[6])
    builder.connect(n.inputs[1], n12.outputs[7])

    n14 = n = builder.newNode(bpy.types.ShaderNodeMixRGB, name='AOMult')
    nodes.add(n)
    n.parent = n0
    n.location = (253.971, -102.884)
    n.hide = 1.000
    n.blend_type = 'MULTIPLY'
    builder.connect(n.inputs[0], 1.000)
    builder.connect(n.inputs[1], n11.outputs[0])
    builder.connect(n.inputs[2], n13.outputs[0])

    n15 = n = builder.newNode(bpy.types.NodeReroute, name='RR.Roughness', label='Roughness')
    nodes.add(n)
    n.location = (-344.332, -188.001)
    n.width = 16.000
    builder.connect(n.inputs[0], n2.outputs[0])

    n16 = n = builder.newNode(bpy.types.NodeGroupInput, name='Input.Normal', create=CreateMode.REUSE)
    nodes.add(n)
    n.location = (-1192.598, 95.180)
    n.width = 188.236
    for i in [0, 1, 4, 6, 7, 8, 9, 10, 11, 12, 13]:
        n.outputs[i].hide = True

    n17 = n = builder.newNode(bpy.types.ShaderNodeNormalMap, name='NormalMap')
    nodes.add(n)
    n.location = (-914.181, 194.816)
    n.use_custom_color = True
    n.color = (0.437, 0.565, 0.828)
    n.width = 150.000
    n.space = 'TANGENT'
    builder.connect(n.inputs[0], n16.outputs[3])
    builder.connect(n.inputs[1], n16.outputs[2])

    n18 = n = builder.newNode(bpy.types.NodeReroute, name='RR.NormalA')
    nodes.add(n)
    n.location = (-676.992, 158.931)
    n.width = 16.000
    builder.connect(n.inputs[0], n17.outputs[0])

    n19 = n = builder.newNode(bpy.types.NodeReroute, name='RR.NormalB')
    nodes.add(n)
    n.location = (-204.326, 157.539)
    n.width = 16.000
    builder.connect(n.inputs[0], n18.outputs[0])

    n20 = n = builder.newNode(bpy.types.NodeFrame, name='F.Dielectric', label='Dielectric')
    nodes.add(n)
    n.location = (173.777, 159.791)
    n.use_custom_color = True
    n.color = (0.574, 0.787, 0.533)
    n.width = 516.374

    n21 = n = builder.newNode(bpy.types.ShaderNodeBsdfDiffuse, name='DielectricDiffuseBSDF')
    nodes.add(n)
    n.parent = n20
    n.location = (-200.000, 100.000)
    n.width = 150.000
    builder.connect(n.inputs[0], n14.outputs[0])
    builder.connect(n.inputs[1], n15.outputs[0])
    builder.connect(n.inputs[2], n19.outputs[0])

    n22 = n = builder.newNode(bpy.types.NodeFrame, name='F.Emission', label='Emission')
    nodes.add(n)
    n.location = (284.633, -859.916)
    n.use_custom_color = True
    n.color = (0.971, 0.671, 1.000)
    n.width = 694.833

    n23 = n = builder.newNode(bpy.types.NodeGroupInput, name='Input.Emission', create=CreateMode.REUSE)
    nodes.add(n)
    n.parent = n22
    n.location = (-349.815, 76.172)
    n.width = 194.717
    for i in [0, 1, 2, 3, 4, 5, 6, 7, 12, 13]:
        n.outputs[i].hide = True

    n24 = n = builder.newNode(bpy.types.NodeReroute, name='RR.EmissiveColor')
    nodes.add(n)
    n.parent = n22
    n.location = (-368.596, -92.881)
    n.width = 16.000
    builder.connect(n.inputs[0], n11.outputs[0])

    n25 = n = builder.newNode(bpy.types.ShaderNodeMixRGB, name='EmissionMixColor')
    nodes.add(n)
    n.parent = n22
    n.location = (-70.514, 34.189)
    n.blend_type = 'MIX'
    n.use_clamp = True
    builder.connect(n.inputs[0], n23.outputs[10])
    builder.connect(n.inputs[1], n24.outputs[0])
    builder.connect(n.inputs[2], n23.outputs[11])

    n26 = n = builder.newNode(bpy.types.ShaderNodeEmission, name='Emissions')
    nodes.add(n)
    n.parent = n22
    n.location = (118.237, 52.939)
    builder.connect(n.inputs[0], n25.outputs[0])
    builder.connect(n.inputs[1], n23.outputs[9])

    n27 = n = builder.newNode(bpy.types.NodeReroute, name='RR.Metalness', label='Metalness')
    nodes.add(n)
    n.location = (-101.540, -251.386)
    n.width = 16.000
    builder.connect(n.inputs[0], n1.outputs[1])

    n28 = n = builder.newNode(bpy.types.NodeFrame, name='F.Metallic', label='Metallic')
    nodes.add(n)
    n.location = (177.282, -417.419)
    n.use_custom_color = True
    n.color = (0.436, 0.649, 0.674)
    n.width = 536.163

    n29 = n = builder.newNode(bpy.types.ShaderNodeFresnel, name='Fresnel')
    nodes.add(n)
    n.location = (-560.916, 70.147)
    n.use_custom_color = True
    n.color = (0.761, 0.761, 0.761)
    builder.connect(n.inputs[0], n16.outputs[5])
    builder.connect(n.inputs[1], n18.outputs[0])

    n30 = n = builder.newNode(bpy.types.ShaderNodeMixRGB, name='MetallicMixColor')
    nodes.add(n)
    n.parent = n28
    n.location = (-193.397, 91.902)
    n.blend_type = 'MIX'
    builder.connect(n.inputs[0], n29.outputs[0])
    builder.connect(n.inputs[1], n27.outputs[0])
    builder.connect(n.inputs[2], (1.000,1.000,1.000,1.000))

    n31 = n = builder.newNode(bpy.types.ShaderNodeBsdfGlossy, name='MetallicGlossyBSDF')
    nodes.add(n)
    n.parent = n28
    n.location = (-200.000, -100.000)
    n.width = 150.000
    n.distribution = 'ASHIKHMIN_SHIRLEY'
    builder.connect(n.inputs[0], n11.outputs[0])
    builder.connect(n.inputs[1], n15.outputs[0])
    builder.connect(n.inputs[2], n19.outputs[0])

    n32 = n = builder.newNode(bpy.types.ShaderNodeMixShader, name='MetallicMixShader')
    nodes.add(n)
    n.parent = n28
    n.location = (136.163, 1.824)
    builder.connect(n.inputs[0], n30.outputs[0])
    builder.connect(n.inputs[1], n21.outputs[0])
    builder.connect(n.inputs[2], n31.outputs[0])

    n33 = n = builder.newNode(bpy.types.ShaderNodeBsdfGlossy, name='DieletricGlossBSDF')
    nodes.add(n)
    n.parent = n20
    n.location = (-200.000, -200.000)
    n.width = 150.000
    n.distribution = 'ASHIKHMIN_SHIRLEY'
    builder.connect(n.inputs[0], (0.800,0.800,0.800,1.000))
    builder.connect(n.inputs[1], n15.outputs[0])
    builder.connect(n.inputs[2], n19.outputs[0])

    n34 = n = builder.newNode(bpy.types.ShaderNodeMixShader, name='DieletricMixShader')
    nodes.add(n)
    n.parent = n20
    n.location = (116.374, -45.923)
    builder.connect(n.inputs[0], n29.outputs[0])
    builder.connect(n.inputs[1], n21.outputs[0])
    builder.connect(n.inputs[2], n33.outputs[0])

    n35 = n = builder.newNode(bpy.types.NodeReroute, name='RR.Emissivity.Map')
    nodes.add(n)
    n.parent = n22
    n.location = (99.899, 88.951)
    n.width = 16.000
    builder.connect(n.inputs[0], n23.outputs[8])

    n36 = n = builder.newNode(bpy.types.ShaderNodeMixShader, name='ComponentsMixShader')
    nodes.add(n)
    n.location = (649.201, -194.458)
    builder.connect(n.inputs[0], n27.outputs[0])
    builder.connect(n.inputs[1], n34.outputs[0])
    builder.connect(n.inputs[2], n32.outputs[0])

    n37 = n = builder.newNode(bpy.types.ShaderNodeMixShader, name='EmmissionMixShader')
    nodes.add(n)
    n.location = (877.730, -497.552)
    builder.connect(n.inputs[0], n35.outputs[0])
    builder.connect(n.inputs[1], n36.outputs[0])
    builder.connect(n.inputs[2], n26.outputs[0])

    n38 = n = builder.newNode(bpy.types.NodeGroupOutput, name='Output', create=CreateMode.REUSE)
    nodes.add(n)
    n.location = (1162.926, -503.416)
    builder.connect(n.inputs[0], n37.outputs[0])

    oldNodes = set(builder.tree.nodes) - nodes
    for n in oldNodes:
        builder.tree.nodes.remove(n)

    pbr.use_fake_user = True

def createDx9ShaderGroup():
    pbr = firstMatching(bpy.data.node_groups, bpy.types.ShaderNodeTree, DX9_NAME)
    if not pbr:
        pbr = bpy.data.node_groups.new(DX9_NAME, blId(bpy.types.ShaderNodeTree))
    builder = ShaderNodesBuilder(pbr, defaultCreate=CreateMode.REUSE)

    # ------------------------------------------------------------------------------------------------------------#
    # input and output sockets of the node-group

    socketSpecs = [
        # DiffuseTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Diffuse", (0.8, 0.8, 0.8, 1.0)),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Emissive", 1.0, 0.0, 1.0), # SE considers 1.0 as "not emissive"
        # NormalTexture
        SocketSpec(bpy.types.NodeSocketColor,         "Normal Map", (0.5, 0.5, 1.0, 1.0)), # straight up
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Specularity", 0.0, 0.0, 1.0),
        # static values
        SocketSpec(bpy.types.NodeSocketColor,         "Uniform Color",      (1, 1, 1, 1)),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Specular Intensity", 0.0, 0.0, 1000.0),
        SocketSpec(bpy.types.NodeSocketFloatUnsigned, "Specular Power",     0.0, 0.0, 1000.0),
    ]
    diffuse, emissivity, normalMap, specularity, uniColor, specInt, specPow = \
        builder.newTreeSockets(socketSpecs, False, (-400, 0))

    socketSpecs = [
        SocketSpec(bpy.types.NodeSocketShader, "Surface")
    ]
    shader = builder.newTreeSockets(socketSpecs, True, (200, 0))[0]

    # ------------------------------------------------------------------------------------------------------------#

    normal = builder.newNormalMap("NormalMap", None, (-200, -200), NormalSpace.TANGENT, None, None, normalMap)

    diffuseColor = builder.newDiffuse(None, "Diffuse Color", (-200, 0), diffuse, 0.0, normal)

    builder.connectSockets([(diffuseColor, shader)])
    pbr.use_fake_user = True


def getDx11Shader(create=True):
    nodeTrees = (tree for tree in bpy.data.node_groups if tree.name.startswith(DX11_NAME))
    try:
        return max(nodeTrees, key=lambda t: t.name) # get the latest version
    except ValueError:
        if not create:
            return None
        createDx11ShaderGroup()
        return getDx11Shader()

def getDx9Shader(create=True):
    nodeTrees = (tree for tree in bpy.data.node_groups if tree.name.startswith(DX9_NAME))
    try:
        return max(nodeTrees, key=lambda t: t.name) # get the latest version
    except ValueError:
        if not create:
            return None
        createDx9ShaderGroup()
        return getDx9Shader()

def getDx11ShaderGroup(tree: bpy.types.ShaderNodeTree):
    return firstMatching(tree.nodes, bpy.types.ShaderNodeGroup, "DX11Shader")

def getDx9ShaderGroup(tree: bpy.types.ShaderNodeTree):
    return firstMatching(tree.nodes, bpy.types.ShaderNodeGroup, "DX9Shader")

def createMaterialNodeTree(tree: bpy.types.ShaderNodeTree):
    builder = ShaderNodesBuilder(tree, defaultCreate=CreateMode.REUSE)
    # tree.nodes.clear()

    def label1(type):
        return type.name + "Texture"
    def label2(type):
        return type.name + "2Texture"

    cmC, _     = builder.newImageTexture(None, label1(TextureType.ColorMetal),  (-200, 600), ImageColorspace.COLOR)
    _, cmA     = builder.newImageTexture(None, label2(TextureType.ColorMetal),  (-200, 550), ImageColorspace.COLOR)
    ngC, _     = builder.newImageTexture(None, label1(TextureType.NormalGloss), (-200, 500), ImageColorspace.NONE)
    _, ngA     = builder.newImageTexture(None, label2(TextureType.NormalGloss), (-200, 450), ImageColorspace.NONE)
    addC, _    = builder.newImageTexture(None, label1(TextureType.AddMaps),     (-200, 400), ImageColorspace.NONE)
    _, addA    = builder.newImageTexture(None, label2(TextureType.AddMaps),     (-200, 350), ImageColorspace.NONE)
    alphaC, _  = builder.newImageTexture(None, label1(TextureType.Alphamask),   (-200, 300), ImageColorspace.NONE)

    addR, addG, _ = builder.newSeparateRgb(None, "Split AddMaps", (0, 400), addC)
    addR.node.inputs[0].default_value = (1, 0, 0, 1) # R: no AO, G: no emissivity, B and A unused

    dx11 = builder.newNode(bpy.types.ShaderNodeGroup, "DX11Shader", None, (250, 600), create=CreateMode.REPLACE)
    dx11.node_tree = getDx11Shader()
    builder.connectSockets(pair for pair in zip(
        [cmC, cmA, ngC, None, ngA, None, addR, None, addG, None, None, None, addA, None],
        dx11.inputs))
    dx11.width = 207

    frameDx11 = builder.newNode(bpy.types.NodeFrame, "DX11Frame", 'DirectX 11 Textures')
    frameDx11.color = (0.30, 0.50, 0.66)
    frameDx11.use_custom_color = True
    frameDx11.shrink = True
    frameDx11.label_size = 25
    for n in (cmC.node, cmA.node, ngC.node, ngA.node, addC.node, addA.node, addR.node, alphaC.node):
        n.parent = frameDx11
        n.hide = True
        n.width_hidden = 100
    dx11.parent = frameDx11

    deC, _  = builder.newImageTexture(None, label1(TextureType.Diffuse), (-200, -100), ImageColorspace.COLOR)
    _, deA  = builder.newImageTexture(None, label2(TextureType.Diffuse), (-200, -150), ImageColorspace.COLOR)
    nsC, _  = builder.newImageTexture(None, label1(TextureType.Normal),  (-200, -200), ImageColorspace.NONE)
    _, nsA  = builder.newImageTexture(None, label2(TextureType.Normal),  (-200, -250), ImageColorspace.NONE)

    uniColor = builder.newRgbValue  (None, "Diffuse Color",      (-200, -300), (1,1,1,1))
    specInt  = builder.newFloatValue(None, "Specular Intensity", (   0, -300), 0.0)
    specPow  = builder.newFloatValue(None, "Specular Power",     (   0, -400), 0.0)

    dx9 = builder.newNode(bpy.types.ShaderNodeGroup, "DX9Shader", None, (250, -50), create=CreateMode.REPLACE)
    dx9.node_tree = getDx9Shader()
    builder.connectSockets(pair for pair in zip(
        [deC, deA, nsC, nsA, uniColor, specInt, specPow],
        dx9.inputs[0:7]))
    dx9.width = 207

    frameDx9 = builder.newNode(bpy.types.NodeFrame, "DX9Frame", 'DirectX 9 Textures')
    frameDx9.color = (0.67, 0.67, 0.39)
    frameDx9.use_custom_color = True
    frameDx9.shrink = True
    frameDx9.label_size = 25
    for n in (deC.node, deA.node, nsC.node, nsA.node):
        n.parent = frameDx9
        n.hide = True
        n.width_hidden = 100
    for n in (uniColor.node, specInt.node, specPow.node, dx9):
        n.parent = frameDx9

    shaderToggle = builder.newMix(None, "Shader Toggle", (600, 150), 0.0, dx11.outputs[0], dx9.outputs[0])

    out = builder.newNode(bpy.types.ShaderNodeOutputMaterial, None, None, (800, 150))
    builder.connectSockets([(shaderToggle, out.inputs[0])])

    # there might be a single leftover Diffuse shader from Blender's default material layout
    # remove it if it isn't connected to anything
    diffuseShader = firstMatching(tree.nodes, bpy.types.ShaderNodeBsdfDiffuse)
    if diffuseShader \
            and not any(input for input in diffuseShader.inputs if len(input.links) > 0) \
            and not any(output for output in diffuseShader.outputs if len(output.links) > 0):
        tree.nodes.remove(diffuseShader)

def register():
    pass

def unregister():
    pass
