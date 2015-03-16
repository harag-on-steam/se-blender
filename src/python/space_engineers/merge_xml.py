from collections import OrderedDict
from enum import Enum
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Comment, ProcessingInstruction, QName, _escape_attrib, _escape_cdata
import os
import bpy

class CommentableTreeBuilder(ET.TreeBuilder):
    def comment(self, data):
       self.start(ET.Comment, {})
       self.data(data)
       self.end(ET.Comment)

# WTF?! ElementTree overrides the Python declaration of XMLParser with some optimized-C version.
# Putting overriding methods on a class derived from that simply doesn't work.
# So we need to copy the whole fucking implementation of XMLParser where overriding ONE method would have sufficed!
class AttributeOrderPreservingParser:
    def __init__(self, target=None, encoding=None):
        try:
            from xml.parsers import expat
        except ImportError:
            try:
                import pyexpat as expat
            except ImportError:
                raise ImportError(
                    "No module named expat; use SimpleXMLTreeBuilder instead"
                    )
        parser = expat.ParserCreate(encoding, "}")
        if target is None:
            target = ET.TreeBuilder()
        self.parser = parser
        self.target = target
        self._error = expat.error
        self._names = {} # name memo cache
        # main callbacks
        parser.DefaultHandlerExpand = self._default
        if hasattr(target, 'start'):
            parser.StartElementHandler = self._start
        if hasattr(target, 'end'):
            parser.EndElementHandler = self._end
        if hasattr(target, 'data'):
            parser.CharacterDataHandler = target.data
        if hasattr(target, 'comment'):
            parser.CommentHandler = target.comment
        if hasattr(target, 'pi'):
            parser.ProcessingInstructionHandler = target.pi
        # Configure pyexpat: buffering, new-style attribute handling.
        parser.buffer_text = 1
        parser.ordered_attributes = 1
        parser.specified_attributes = 1
        self._doctype = None
        self.entity = {}
        try:
            self.version = "Expat %d.%d.%d" % expat.version_info
        except AttributeError:
            pass # unknown

    def _raiseerror(self, value):
        err = ET.ParseError(value)
        err.code = value.code
        err.position = value.lineno, value.offset
        raise err

    def _fixname(self, key):
        # expand qname, and convert name string to ascii, if possible
        try:
            name = self._names[key]
        except KeyError:
            name = key
            if "}" in name:
                name = "{" + name
            self._names[key] = name
        return name

    def _start(self, tag, attr_list):
        fixname = self._fixname
        tag = fixname(tag)
        attrib = OrderedDict() # <- this is the changed line
        if attr_list:
            for i in range(0, len(attr_list), 2):
                attrib[fixname(attr_list[i])] = attr_list[i+1]
        return self.target.start(tag, attrib)

    def _end(self, tag):
        return self.target.end(self._fixname(tag))

    def _default(self, text):
        prefix = text[:1]
        if prefix == "&":
            # deal with undefined entities
            try:
                data_handler = self.target.data
            except AttributeError:
                return
            try:
                data_handler(self.entity[text[1:-1]])
            except KeyError:
                from xml.parsers import expat
                err = expat.error(
                    "undefined entity %s: line %d, column %d" %
                    (text, self.parser.ErrorLineNumber,
                    self.parser.ErrorColumnNumber)
                    )
                err.code = 11 # XML_ERROR_UNDEFINED_ENTITY
                err.lineno = self.parser.ErrorLineNumber
                err.offset = self.parser.ErrorColumnNumber
                raise err
        elif prefix == "<" and text[:9] == "<!DOCTYPE":
            self._doctype = [] # inside a doctype declaration
        elif self._doctype is not None:
            # parse doctype contents
            if prefix == ">":
                self._doctype = None
                return
            text = text.strip()
            if not text:
                return
            self._doctype.append(text)
            n = len(self._doctype)
            if n > 2:
                type = self._doctype[1]
                if type == "PUBLIC" and n == 4:
                    name, type, pubid, system = self._doctype
                    if pubid:
                        pubid = pubid[1:-1]
                elif type == "SYSTEM" and n == 3:
                    name, type, system = self._doctype
                    pubid = None
                else:
                    return
                if hasattr(self.target, "doctype"):
                    self.target.doctype(name, pubid, system[1:-1])
                self._doctype = None

    def feed(self, data):
        """Feed encoded data to parser."""
        try:
            self.parser.Parse(data, 0)
        except self._error as v:
            self._raiseerror(v)

    def close(self):
        """Finish feeding data to parser and return element structure."""
        try:
            self.parser.Parse("", 1) # end of data
        except self._error as v:
            self._raiseerror(v)
        try:
            close_handler = self.target.close
        except AttributeError:
            pass
        else:
            return close_handler()
        finally:
            # get rid of circular references
            del self.parser
            del self.target

# again, one line changed :(
def _serialize_xml(write, elem, qnames, namespaces,
                   short_empty_elements, **kwargs):
    tag = elem.tag
    text = elem.text
    if tag is Comment:
        write("<!--%s-->" % text)
    elif tag is ProcessingInstruction:
        write("<?%s?>" % text)
    else:
        tag = qnames[tag]
        if tag is None:
            if text:
                write(_escape_cdata(text))
            for e in elem:
                _serialize_xml(write, e, qnames, None,
                               short_empty_elements=short_empty_elements)
        else:
            write("<" + tag)
            items = list(elem.items())
            if items or namespaces:
                if namespaces:
                    for v, k in sorted(namespaces.items(),
                                       key=lambda x: x[1]):  # sort on prefix
                        if k:
                            k = ":" + k
                        write(" xmlns%s=\"%s\"" % (
                            k,
                            _escape_attrib(v)
                            ))

                # below is the changed line. assuming attrib is an OrderedDict this will preserve attribute order
                for k, v in elem.attrib.items():
                    if isinstance(k, QName):
                        k = k.text
                    if isinstance(v, QName):
                        v = qnames[v.text]
                    else:
                        v = _escape_attrib(v)
                    write(" %s=\"%s\"" % (qnames[k], v))
            if text or len(elem) or not short_empty_elements:
                write(">")
                if text:
                    write(_escape_cdata(text))
                for e in elem:
                    _serialize_xml(write, e, qnames, None,
                                   short_empty_elements=short_empty_elements)
                write("</" + tag + ">")
            else:
                write(" />")
    if elem.tail:
        write(_escape_cdata(elem.tail))

def _serialize_xml_with_xml_decl(write, elem, qnames, namespaces,
                               short_empty_elements, **kwargs):
    # ElementTree is to dumb to honor the xml_declaration for other methods than 'xml' :(
    write("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n")
    _serialize_xml(write, elem, qnames, namespaces, short_empty_elements, **kwargs)

# At least we don't need to monkey-patch... Register as an additional serialization-method.
ET._serialize['ordered-attribs'] = _serialize_xml_with_xml_decl

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
                base[index-1].tail = "\n" + (indent * (level+1))
            else:
                result.tail = "\n" + (indent * (level+1))
        else:
            base.text = "\n" + (indent * (level+1))
            result.tail = "\n" + (indent * level)

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

BLOCK_ELEMENTS = [
    'Id', 'DisplayName', 'Icon', 'CubeSize', 'BlockTopology', 'Size', 'ModelOffset', 'Model', 'UseModelIntersection',
    'Components', 'CriticalComponent', 'BuildProgressModels', 'MountPoints', 'BlockPairName',
    'MirroringBlock', 'MirroringX', 'MirroringY', 'MirroringZ', 'DeformationRatio', 'EdgeType',
    'BuildTimeSeconds', 'DisassembleRatio', 'Public',
]

ID_ELEMENTS = ['TypeId', 'SubtypeId']

LIST_ELEMENTS = {'BuildProgressModels', 'MountPoints'}

class MergeResult(Enum):
    MERGED = 1
    RENAMED = 2
    NOT_FOUND = 3

class CubeBlocksMerger:
    def __init__(self, cubeBlocksPath: str, indent="    ", backup=True, allowRenames=False):
        self.path = bpy.path.abspath(cubeBlocksPath)
        self.tree = ET.parse(cubeBlocksPath, parser=AttributeOrderPreservingParser(target=CommentableTreeBuilder()))
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

        blockEditor = XmlEditor(BLOCK_ELEMENTS, indentLevel=2, indent="\t")
        listEditor = XmlEditor([], indentLevel=3, indent="\t")
        idEditor = XmlEditor(ID_ELEMENTS, indentLevel=3, indent="\t")

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

        self.tree.write(self.path, encoding="utf-8", xml_declaration=False, method="ordered-attribs")
