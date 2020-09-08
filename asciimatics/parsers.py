
"""
This module provides parsers to create ColouredText objects from embedded control strings.
"""
from __future__ import division
from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals
import re
from builtins import str
from future.utils import with_metaclass
from abc import ABCMeta, abstractmethod
from logging import getLogger
import asciimatics.constants as constants
from asciimatics.utilities import _DotDict


# Diagnostic logging
logger = getLogger(__name__)


class Parser(with_metaclass(ABCMeta, object)):
    """
    Abstract class to represent text parsers that extract colour control codes from raw text and
    convert them to displayable text and associated colour maps.
    """

    def __init__(self):
        """
        Initialize the parser.
        """
        self._raw_text = ""
        self._attributes = None
        self._cursor = 0
        self._result = []

    def reset(self, text, colours):
        """
        Reset the parser to analyze the supplied raw text.

        :param text: raw text to process.
        :param colours: colour tuple to initialise the colour map.
        """
        self._raw_text = text
        self._attributes = colours
        self._cursor = 0
        self._result = []

    @property
    def cursor(self):
        """
        Location of cursor in the parsed text.
        """
        return self._cursor

    @abstractmethod
    def normalize(self):
        """
        Return the normalized version of the raw string.
        """

    def parse(self):
        """
        Generator to return coloured text from raw text.

        Generally returns a stream of text/color tuple/offset tuples.  If there is a colour update with no
        visible text, the first element of the tuple may be None.

        :returns: a 3-tuple of (the displayable text, associated colour tuple, start offset in raw text)
        """
        for element in self._result:
            yield tuple(element)


class ControlCodeParser(Parser):
    """
    Parser to replace all control codes with a readable version - e.g. "^M" for \\r.
    """

    def reset(self, text, colours=None):
        """
        Reset the parser to analyze the supplied raw text.

        :param text: raw text to process.
        :param colours: colour tuple to initialise the colour map.
        """
        super(ControlCodeParser, self).reset(text, colours)
        attributes = iter(self._attributes) if self._attributes else (None, None, None)
        for i, letter in enumerate(text):
            if ord(letter) < 32:
                self._result.append(
                    ("^" + chr(ord("@") + ord(letter)), attributes, i))
            else:
                self._result.append((letter, attributes, i))

    def normalize(self):
        """
        Return the normalized version of the raw string.
        """
        return self._raw_text


class AsciimaticsParser(Parser):
    """
    Parser to handle Asciimatics rendering escape strings.
    """

    # Regular expression for use to find colour sequences in multi-colour text.
    # It should match ${n}, ${m,n} or ${m,n,o}
    _colour_sequence = re.compile(constants.COLOUR_REGEX)

    def reset(self, text, colours):
        """
        Reset the parser to analyze the supplied raw text.

        :param text: raw text to process.
        :param colours: colour tuple to initialise the colour map.
        """
        super(AsciimaticsParser, self).reset(text, colours)
        attributes = [x for x in self._attributes] if self._attributes else [None, None, None]
        offset = last_offset = 0
        text = self._raw_text
        while len(text) > 0:
            match = self._colour_sequence.match(str(text))
            if match is None:
                self._result.append((text[0], tuple(attributes), last_offset))
                text = text[1:]
                offset += 1
                last_offset = offset
            else:
                # The regexp either matches:
                # - 2,3,4 for ${c,a,b}
                # - 5,6 for ${c,a}
                # - 7 for ${c}.
                if match.group(2) is not None:
                    attributes = (int(match.group(2)),
                                  constants.MAPPING_ATTRIBUTES[match.group(3)],
                                  int(match.group(4)))
                elif match.group(5) is not None:
                    attributes = (int(match.group(5)),
                                  constants.MAPPING_ATTRIBUTES[match.group(6)],
                                  None)
                else:
                    attributes = (int(match.group(7)), 0, None)
                offset += 3 + len(match.group(1))
                text = match.group(8)

        # Save off cursor position
        self._cursor = len(self._result)

        # Add final colours if needed.
        if last_offset != offset:
            self._result.append([None, tuple(attributes), last_offset])

    def normalize(self):
        """
        Return the normalized version of the raw string.
        """
        return self._raw_text


class AnsiTerminalParser(Parser):
    """
    Parser to handle ANSI terminal escape codes.
    """

    # Regular expression for use to find colour sequences in multi-colour text.
    _colour_sequence = re.compile(r"^(\x1B\[([^@-~]*)([@-~]))(.*)")

    def reset(self, text, colours):
        """
        Reset the parser to analyze the supplied raw text.

        :param text: raw text to process.
        :param colours: colour tuple to initialise the colour map.
        """
        super(AnsiTerminalParser, self).reset(text, colours)
        state = _DotDict()
        state.result = []
        state.attributes = [x for x in self._attributes] if self._attributes else [None, None, None]
        state.offset = 0
        state.last_offset = 0
        state.cursor = 0
        state.text = self._raw_text

        def _handle_escape(st):
            match = self._colour_sequence.match(str(st.text))
            if match is None:
                # Escape - ignore next char as a minimal way to handle many sequences
                return 2
            else:
                if match.group(3) == "m":
                    # We have found a SGR escape sequence ( CSI ... m ).  These have zero or more
                    # embedded arguments, so create a simple FSM to process the parameter stream.
                    in_set_mode = False
                    in_index_mode = False
                    in_rgb_mode = False
                    skip_size = 0
                    attribute_index = 0
                    for parameter in match.group(2).split(";"):
                        try:
                            parameter = int(parameter)
                        except ValueError:
                            parameter = 0
                        if in_set_mode:
                            # We are processing a set fore/background colour code
                            if parameter == 5:
                                in_index_mode = True
                            elif parameter == 2:
                                in_rgb_mode = True
                                skip_size = 3
                            else:
                                logger.info(("Unexpected colour setting", parameter))
                            in_set_mode = False
                        elif in_index_mode:
                            # We are processing a 5;n sequence for colour indeces
                            st.attributes[attribute_index] = parameter
                            in_index_mode = False
                        elif in_rgb_mode:
                            # We are processing a 2;r;g;b sequence for RGB colours - just ignore.
                            skip_size -= 1
                            if skip_size <= 0:
                                in_rgb_mode = False
                        else:
                            # top-level stream processing
                            if parameter == 0:
                                # Reset
                                st.attributes = [constants.COLOUR_WHITE,
                                                 constants.A_NORMAL,
                                                 constants.COLOUR_BLACK]
                            elif parameter == 1:
                                # Bold
                                st.attributes[1] = constants.A_BOLD
                            elif parameter in (2, 22):
                                # Faint/normal - faint not supported so treat as normal
                                st.attributes[1] = constants.A_NORMAL
                            elif parameter == 7:
                                # Inverse
                                st.attributes[1] = constants.A_REVERSE
                            elif parameter == 27:
                                # Inverse off - assume that means normal
                                st.attributes[1] = constants.A_NORMAL
                            elif parameter in range(30, 38):
                                # Standard foreground colours
                                st.attributes[0] = parameter - 30
                            elif parameter in range(40, 48):
                                # Standard background colours
                                st.attributes[2] = parameter - 40
                            elif parameter == 38:
                                # Set foreground colour - next parameter is either 5 (index) or 2 (RGB color)
                                in_set_mode = True
                                attribute_index = 0
                            elif parameter == 48:
                                # Set background colour - next parameter is either 5 (index) or 2 (RGB color)
                                in_set_mode = True
                                attribute_index = 2
                            else:
                                logger.debug("Ignoring parameter: %s", parameter)
                elif match.group(3) == "K":
                    # This is a line delete sequence.  Parameter defines which parts to delete.
                    param = match.group(2)
                    if param in ("", "0"):
                        st.result = st.result[:st.cursor]
                    elif param == "1":
                        st.result = [[" ", tuple(st.attributes), st.offset] for _ in range(st.cursor)] + \
                                     st.result[st.cursor:]
                    elif param == "2":
                        st.result = [[" ", tuple(st.attributes), st.offset] for _ in range(st.cursor)]
                elif match.group(3) == "P":
                    # This is a character delete sequence.  Parameter defines how many to delete.
                    param = 1 if match.group(2) == "" else int(match.group(2))
                    st.result = st.result[:st.cursor] + st.result[st.cursor + param:]
                elif match.group(3) == "C":
                    # Move cursor forwards.  Parameter defines how far to move..
                    param = 1 if match.group(2) == "" else int(match.group(2))
                    st.cursor += param
                elif match.group(3) == "D":
                    # Move cursor backwards.  Parameter defines how far to move..
                    param = 1 if match.group(2) == "" else int(match.group(2))
                    st.cursor -= param
                else:
                    logger.debug("Ignoring control: %s", match.group(3))
                return len(match.group(1))

        while len(state.text) > 0:
            char = ord(state.text[0])
            new_offset = 1
            if char > 31:
                state.result[state.cursor:state.cursor + 1] = [
                    [state.text[0], tuple(state.attributes), state.last_offset]]
                state.cursor += 1
                state.last_offset = state.offset + 1
            elif char == 8:
                # Back space
                state.cursor = max(state.cursor - 1, 0)
            elif char == 13:
                # Carriage return
                state.cursor = 0
            elif char == 27:
                new_offset = _handle_escape(state)
            else:
                logger.debug("Ignoring character: %d", char)
            state.offset += new_offset
            state.text = state.text[new_offset:]

        self._result = state.result
        self._cursor = len(state.result) - state.cursor
        if state.last_offset != state.offset:
            self._result.append([None, tuple(state.attributes), state.last_offset])

    def normalize(self):
        new_value = ""
        attributes = None, None, None
        last_offset = 0
        for i, element in enumerate(self._result):
            # Convert parsed output to the simplest form
            if attributes != element[1]:
                format = []
                if attributes[0] != element[1][0]:
                    format.append("38;5;{}".format(element[1][0]))
                if attributes[1] != element[1][1]:
                    if element[1][1] == constants.A_BOLD:
                        format.append("1")
                    elif element[1][1] == constants.A_NORMAL:
                        format.append("2")
                    elif element[1][1] == constants.A_REVERSE:
                        format.append("7")
                if attributes[2] != element[1][2]:
                    format.append("48;5;{}".format(element[1][2]))
                new_value += "\x1B[{}m".format(";".join(format))
                attributes = element[1]
            if element[0] is not None:
                new_value += element[0]
            self._result[i][2] = last_offset
            last_offset = len(new_value)
        if self._cursor > 0:
            new_value += "\x1B[{}D".format(self._cursor)
        self._raw_text = new_value
        return new_value
