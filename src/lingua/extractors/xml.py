from __future__ import absolute_import
from __future__ import print_function
import collections
import re
import sys
from io import BytesIO
from xml.parsers import expat
from .python import extract_python
from . import register_extractor
from . import Message


class TranslateContext(object):
    WHITESPACE = re.compile(u"\s{2,}")
    EXPRESSION = re.compile(u"\s*\${[^}]*}\s*")

    def __init__(self, domain, msgid, filename, lineno, i18n_prefix):
        self.domain = domain
        self.msgid = msgid
        self.text = []
        self.filename = filename
        self.lineno = lineno
        self.i18n_prefix = i18n_prefix

    def addText(self, text):
        self.text.append(text)

    def addNode(self, name, attributes):
        name = attributes.get('%s:name' % self.i18n_prefix)
        if name:
            self.text.append(u'${%s}' % name)
        else:
            self.text.append(u'<dynamic element>')

    def ignore(self):
        text = u''.join(self.text).strip()
        text = self.WHITESPACE.sub(u' ', text)
        text = self.EXPRESSION.sub(u'', text)
        return not text

    def message(self):
        text = u''.join(self.text).strip()
        text = self.WHITESPACE.sub(u' ', text)
        if not self.msgid:
            self.msgid = text
            text = u''
        comment = u'Default: %s' % text if text else u''
        return Message(None, self.msgid, u'', [], comment, u'',
                (self.filename, self.lineno))


class XmlExtractor(object):
    ENTITY = re.compile(r"&([A-Za-z]+|#[0-9]+);")
    UNDERSCORE_CALL = re.compile("_\(")

    def __call__(self, filename, options):
        self.filename = filename
        self.target_domain = options.domain
        self.options = options
        self.messages = []
        self.parser = expat.ParserCreate()
        if hasattr(self.parser, 'returns_unicode'):  # Not present in Py3
            self.parser.returns_unicode = True
        self.parser.UseForeignDTD()
        self.parser.SetParamEntityParsing(
            expat.XML_PARAM_ENTITY_PARSING_ALWAYS)
        self.parser.StartElementHandler = self.StartElementHandler
        self.parser.CharacterDataHandler = self.CharacterDataHandler
        self.parser.EndElementHandler = self.EndElementHandler
        self.parser.DefaultHandler = self.DefaultHandler
        self.domainstack = collections.deque()
        self.translatestack = collections.deque([None])
        self.prefix_stack = collections.deque(['i18n'])

        try:
            self.parser.ParseFile(open(filename, 'rb'))
        except expat.ExpatError as e:
            print('Aborting due to parse error in %s: %s' %
                            (filename, e.message), file=sys.stderr)
            sys.exit(1)
        return self.messages

    def add_message(self, msgid, comment=u''):
        self.messages.append(Message(None, msgid, u'', [], comment, u'',
            (self.filename, (self.parser.CurrentLineNumber))))

    def addUnderscoreCalls(self, message):
        msg = message
        if isinstance(msg, unicode):
            msg = msg.encode('utf-8')
        for message in extract_python(BytesIO(msg), {'_': None}, None, None):
            self.messages.append(Message(message[:6],
                (self.filename, self.parser.CurrentLineNumber)))

    def StartElementHandler(self, name, attributes):
        i18n_prefix = self.prefix_stack[-1]
        for (attr, value) in attributes.items():
            if value == 'http://xml.zope.org/namespaces/i18n' and \
                    attr.startswith('xmlns:'):
                i18n_prefix = attr[6:]
        self.prefix_stack.append(i18n_prefix)

        new_domain = attributes.get('%s:domain' % i18n_prefix)
        if i18n_prefix and new_domain:
            self.domainstack.append(new_domain)
        elif self.domainstack:
            self.domainstack.append(self.domainstack[-1])

        if self.translatestack[-1]:
            self.translatestack[-1].addNode(name, attributes)

        i18n_translate = attributes.get('%s:translate' % i18n_prefix)
        if i18n_prefix and i18n_translate is not None:
            self.translatestack.append(TranslateContext(
                self.domainstack[-1] if self.domainstack else None,
                i18n_translate, self.filename, self.parser.CurrentLineNumber,
                i18n_prefix))
        else:
            self.translatestack.append(None)

        if not self.domainstack:
            return

        i18n_attributes = attributes.get('%s:attributes' % i18n_prefix)
        if i18n_prefix and i18n_attributes:
            parts = [p.strip() for p in i18n_attributes.split(';')]
            for msgid in parts:
                if ' ' not in msgid:
                    if msgid not in attributes:
                        continue
                    self.add_message(attributes[msgid])
                else:
                    try:
                        (attr, msgid) = msgid.split()
                    except ValueError:
                        continue
                    if attr not in attributes:
                        continue
                    self.add_message(msgid, u'Default: %s' % attributes[attr])

        for (attr, value) in attributes.items():
            if self.UNDERSCORE_CALL.search(value):
                self.addUnderscoreCalls(value)

    def DefaultHandler(self, data):
        if data.startswith(u'&') and self.translatestack[-1]:
            self.translatestack[-1].addText(data)

    def CharacterDataHandler(self, data):
        if TranslateContext.EXPRESSION.search(data) and \
                self.UNDERSCORE_CALL.search(data):
            self.addUnderscoreCalls(data)
        if not self.translatestack[-1]:
            return

        self.translatestack[-1].addText(data)
        return
#        data_length = len(data)
#        context = self.parser.GetInputContext()
#
#        while data:
#            m = self.ENTITY.search(context)
#            if m is None or m.start() >= data_length:
#                self.translatestack[-1].addText(data)
#                break
#
#            n = self.ENTITY.match(data)
#            if n is not None:
#                length = n.end()
#            else:
#                length = 1
#
#            self.translatestack[-1].addText(context[0: m.end()])
#            data = data[m.start() + length:]

    def EndElementHandler(self, name):
        if self.prefix_stack:
            self.prefix_stack.pop()
        if self.domainstack:
            self.domainstack.pop()
        translate = self.translatestack.pop()
        if translate and not translate.ignore() and  \
                (self.target_domain in [None, translate.domain]):
            self.messages.append(translate.message())


@register_extractor('xml', ['.pt', '.zpt'])
def extract_xml(filename, options):
    extractor = XmlExtractor()
    return extractor(filename, options)
