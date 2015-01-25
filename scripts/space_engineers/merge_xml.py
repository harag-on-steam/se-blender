from enum import Enum
import os
from xml.etree import ElementTree as ET
import bpy


class CommentableTreeBuilder(ET.TreeBuilder):
    def comment(self, data):
       self.start(ET.Comment, {})
       self.data(data)
       self.end(ET.Comment)

BLOCK_ELEMENTS = [
    'Id', 'DisplayName', 'Icon', 'CubeSize', 'BlockTopology', 'Size', 'ModelOffset', 'Model', 'UseModelIntersection',
    'Components', 'CriticalComponent', 'BuildProgressModels', 'MountPoints', 'BlockPairName',
    'MirroringX', 'MirroringY', 'MirroringZ', 'DeformationRatio', 'EdgeType',
    'BuildTimeSeconds', 'DisassembleRatio', 'Public',
]

ID_ELEMENTS = ['TypeId', 'SubtypeId']

LIST_ELEMENTS = {'BuildProgressModels', 'MountPoints'}

class XmlEditor:
    def __init__(self, knownSubelements: list, indentLevel=0, indent="    ", base: ET.Element = None):
        self.knownElements = knownSubelements
        self.knownElemPos = {name : pos for pos, name in enumerate(knownSubelements)}
        self.indentLevel = indentLevel
        self.indent = indent

    def newElement(self, base: ET.Element, index: int, tagName: str) -> ET.Element:
        """
        Creates a new element with the given tagname, properly indents it and inserts it at the given index.
        """
        indent = self.indent
        level = self.indentLevel
        result = ET.Element(tagName)

        # indentation
        if len(base) > 0:
            if index > 0:
                result.tail = base[index-1].tail
                base[index-1].tail = "\r\n" + (indent * (level+1))
            else:
                result.tail = "\r\n" + (indent * (level+1))
        else:
            base.text = "\r\n" + (indent * (level+1))
            result.tail = "\r\n" + (indent * level)

        base.insert(index, result)
        return result

    def find(self, base: ET.Element, tagName: str) -> tuple:
        """
        Finds a singluar subelement with the given tagName
        :returns: (index, subelement) or None
        """
        # runtime O(n). But it's simple, doesn't require additional data-structures and
        # is probably fast enough for small numbers of subelements.
        for i, e in enumerate(base):
            if e.tag == tagName:
                return (i, e)
        return None

    def subelement(self, base: ET.Element, tagName: str) -> ET.Element:
        """
        Finds a singular element with the given tagname or creates a new one if it doesn't exist.
        If the tagname is a known tagname a new element is inserted as close to its known location
        as can be determined from existing known elements.
        If the tagname is unknown the new element is inserted as the last element.
        """
        existing = self.find(base, tagName)
        if existing != None:
            return existing[1]

        pos = self.knownElemPos.get(tagName, -1)
        if pos == -1:
            return self.newElement(base, len(base), tagName)

        numKnown = len(self.knownElements)
        pivot = None
        insertBefore = None

        # which is the closest existing element with a known tagname?
        for look in range(1, max(pos, numKnown-pos)):
            # inserting after a known element is preferred because this is less likely
            # to separate a comment from the element it belongs to
            after = pos-look
            if after >= 0:
                pivot = self.find(base, self.knownElements[after])
                if pivot != None:
                    insertBefore = False
                    break

            before = pos+look
            if before < numKnown:
                pivot = self.find(base, self.knownElements[before])
                if pivot != None:
                    insertBefore = True
                    break

        # no existing, known element to insert at
        if pivot == None:
            return self.newElement(base, len(base), tagName)

        return self.newElement(base, pivot[0] if insertBefore else pivot[0]+1, tagName)

class MergeResult(Enum):
    MERGED = 1
    RENAMED = 2
    NOT_FOUND = 3

class CubeBlocksMerger:
    def __init__(self, cubeBlocksPath: str, indent="    ", backup=True, allowRenames=False):
        self.path = bpy.path.abspath(cubeBlocksPath)
        self.tree = ET.parse(cubeBlocksPath, parser=ET.XMLParser(target=CommentableTreeBuilder()))
        self.backup = backup
        self.allowRenames = allowRenames

        root = self.tree.getroot()
        if root == None or root.tag != "Definitions":
            raise ValueError(cubeBlocksPath + " contains no <Definitions>")

        blockContainer = root.find("CubeBlocks")
        if blockContainer == None:
            raise ValueError(cubeBlocksPath + " contains no <CubeBlocks>")

        self.blocksById = {}
        self.blocksByPairAndSize = {}

        for block in blockContainer.iter("Definition"):
            subtypeId = block.findtext("./Id/SubtypeId", None)
            if subtypeId != None:
                self.blocksById[subtypeId] = block

            pairName = block.findtext("BlockPairName", None)
            size = block.findtext("CubeSize", None)
            if pairName != None and size != None:
                self.blocksByPairAndSize[(pairName, size)] = block

    def merge(self, xml: ET.Element, renameAllowed=False) -> set:
        id = xml.findtext("./Id/SubtypeId", None)
        if (id == None):
            raise ValueError("xml has no <SubtypeId>")

        block = self.blocksById.get(id, None)
        rename = set()

        if block == None and renameAllowed:
            pairName = xml.findtext("BlockPairName", None)
            size = xml.findtext("CubeSize", None)
            if pairName != None and size != None:
                block = self.blocksByPairAndSize.get((pairName, size), None)
                rename = {MergeResult.RENAMED}

        if block == None:
            return {MergeResult.NOT_FOUND}

        blockEditor = XmlEditor(BLOCK_ELEMENTS, 2)
        listEditor = XmlEditor([], 3)
        idEditor = XmlEditor(ID_ELEMENTS, 3)

        for e in xml:
            if e.tag == 'Id':
                idElem = blockEditor.subelement(block, 'Id')
                subIdElem = idEditor.subelement(idElem, 'SubtypeId')
                subIdElem.text = id

            elif e.tag in LIST_ELEMENTS:
                list = blockEditor.subelement(block, e.tag)
                list.attrib = e.attrib

                lastTail = list[-1].tail if len(list) > 0 else None

                list.text = ""
                list[:] = []
                for item in e:
                    itemCopy = listEditor.newElement(list, len(list), item.tag)
                    itemCopy.text = item.text
                    itemCopy.attrib = item.attrib

                if len(list) > 0 and lastTail != None:
                    list[-1].tail = lastTail

            else:
                copy = blockEditor.subelement(block, e.tag)
                copy.text = e.text
                copy.attrib = e.attrib

        for listTag in LIST_ELEMENTS:
            if xml.find(listTag) == None:
                list = block.find(listTag)
                if list != None:
                    block.remove(list)

        return {MergeResult.MERGED} | rename

    def write(self):
        if self.backup:
            if os.path.exists(self.path + ".bak"):
                os.remove(self.path + ".bak")
            os.rename(self.path, self.path + ".bak")

        self.tree.write(self.path, "utf-8")
