"""SCPI command patterns, message splitting and parameter checks.

Numeric suffixes are written <n> in a pattern, as in 'CALCulate<n>:MARKer<m>:X'. These
instruments have one window and one marker, so only suffix 1, or none, is accepted.

Known simplifications: every command in a compound message is read from the root of the
command tree, and parameters can't contain quoted strings. Nothing on the bench sends either.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from string import ascii_uppercase

from simulators.common.errors import ScpiCommandError, ScpiError, ScpiErrorCode

_NAME = r"[A-Za-z]+(?:<[a-z]+>)?"
_PATTERN = re.compile(
    rf"(?:\[\*?{_NAME}\]|\*?{_NAME})"  # first node, optionally in brackets
    rf"(?:\[:{_NAME}\]|:{_NAME})*"  # later nodes, each after a colon
)
_PATTERN_NODE = re.compile(rf"(\[)?:?(\*?{_NAME})\]?")
_ACCEPTED_SUFFIXES = frozenset({"", "1"})


@dataclass(frozen=True)
class _Node:
    """One level of the command tree, such as FREQuency, with its two accepted spellings.

    Attributes:
        short_form: The capital letters of the mnemonic, for example 'FREQ'.
        long_form: The whole mnemonic in capitals, for example 'FREQUENCY'.
        optional: Whether the node may be left out of a header.
        takes_suffix: Whether the node accepts a numeric suffix, written <n> in the pattern.
    """

    short_form: str
    long_form: str
    optional: bool
    takes_suffix: bool = False

    @classmethod
    def from_mnemonic(cls, mnemonic: str, *, optional: bool) -> "_Node":
        """Build a node from SCPI notation, where the capital letters are the short form.

        Args:
            mnemonic: The node as written in the manual, for example 'FREQuency'.
            optional: Whether the node may be left out.

        Returns:
            The node.

        Raises:
            ValueError: If the mnemonic doesn't start with capitals, so it has no short form.
        """
        takes_suffix = mnemonic.endswith(">")
        mnemonic = mnemonic.partition("<")[0]
        if mnemonic.startswith("*"):
            return cls(mnemonic.upper(), mnemonic.upper(), optional)
        short_form = mnemonic[: len(mnemonic) - len(mnemonic.lstrip(ascii_uppercase))]
        if not short_form:
            raise ValueError(f"Mnemonic {mnemonic!r} needs capitals for its short form")
        return cls(short_form, mnemonic.upper(), optional, takes_suffix)

    def accepts(self, token: str) -> bool:
        """Check one header token against this node.

        Args:
            token: One colon-separated part of a received header, in any case.

        Returns:
            True if the token is this node's short or long form, followed by suffix 1 or
            none when the node takes a suffix.
        """
        name = token.rstrip("0123456789") if self.takes_suffix else token
        suffix = token[len(name) :]
        return suffix in _ACCEPTED_SUFFIXES and name.upper() in {self.short_form, self.long_form}


@dataclass(frozen=True)
class CommandPattern:
    """A command written in SCPI manual notation, such as 'SOURce:FREQuency[:CW]'.

    Attributes:
        text: The pattern as written.
        nodes: The pattern's nodes, in order.
    """

    text: str
    nodes: tuple[_Node, ...]

    @classmethod
    def parse(cls, text: str) -> "CommandPattern":
        """Read a pattern in manual notation.

        Args:
            text: The pattern, with optional nodes in square brackets.

        Returns:
            The pattern, ready to match headers.

        Raises:
            ValueError: If the pattern is malformed or uses a numeric suffix; this is a
                programming error, caught when the command is registered.
        """
        if re.search(r"\d", text):
            raise ValueError(f"Write numeric suffixes as <n>, not digits, in pattern {text!r}")
        if not _PATTERN.fullmatch(text):
            raise ValueError(f"Malformed command pattern: {text!r}")
        nodes = tuple(
            _Node.from_mnemonic(match[2], optional=match[1] is not None)
            for match in _PATTERN_NODE.finditer(text)
        )
        return cls(text, nodes)

    def matches(self, header: str) -> bool:
        """Check whether a received header names this command.

        Args:
            header: The header without its question mark, for example ':sour:freq'.

        Returns:
            True if the header is a valid spelling of this command.
        """
        tokens = tuple(header.removeprefix(":").split(":"))
        return _match_nodes(self.nodes, tokens)


def _match_nodes(nodes: tuple[_Node, ...], tokens: tuple[str, ...]) -> bool:
    """Match header tokens against pattern nodes, trying optional nodes both ways.

    Args:
        nodes: The pattern nodes still to match.
        tokens: The header tokens still to match.

    Returns:
        True if the tokens spell out the nodes.
    """
    if not nodes:
        return not tokens
    first, rest = nodes[0], nodes[1:]
    if tokens and first.accepts(tokens[0]) and _match_nodes(rest, tokens[1:]):
        return True
    return first.optional and _match_nodes(rest, tokens)


@dataclass(frozen=True)
class ParsedCommand:
    """One command from a message, split into its parts.

    Attributes:
        header: The command path without the question mark, for example 'SOUR:FREQ'.
        is_query: Whether the command asks for a value.
        parameters: The parameters, trimmed, in order.
    """

    header: str
    is_query: bool
    parameters: tuple[str, ...]


def split_message(line: str) -> list[str]:
    """Split one received line into its commands, dropping empty ones.

    Args:
        line: The line, without its line ending.

    Returns:
        The commands, trimmed.
    """
    return [part.strip() for part in line.split(";") if part.strip()]


def parse_command(text: str) -> ParsedCommand:
    """Split one command into header, query flag and parameters.

    Any whitespace separates the header from the parameters, as IEEE 488.2 allows.

    Args:
        text: A single command, such as 'SOUR:FREQ 1 GHz' or 'SOUR:POW?'.

    Returns:
        The parsed command.

    Raises:
        ScpiCommandError: -102 if the header or a parameter is empty.
    """
    header_and_rest = text.split(maxsplit=1)
    header = header_and_rest[0] if header_and_rest else ""
    parameter_text = header_and_rest[1] if len(header_and_rest) > 1 else ""
    is_query = header.endswith("?")
    path = header.removesuffix("?").removeprefix(":")
    if not path or "" in path.split(":"):
        raise ScpiCommandError(ScpiError(ScpiErrorCode.SYNTAX_ERROR, text))
    parameters = tuple(part.strip() for part in parameter_text.split(",")) if parameter_text else ()
    if "" in parameters:
        raise ScpiCommandError(ScpiError(ScpiErrorCode.SYNTAX_ERROR, text))
    return ParsedCommand(header.removesuffix("?"), is_query, parameters)


def expect_no_parameters(parameters: tuple[str, ...]) -> None:
    """Check that a command received no parameters.

    Args:
        parameters: The parameters the command received.

    Raises:
        ScpiCommandError: -108 if any parameter was given.
    """
    if parameters:
        raise ScpiCommandError(ScpiError(ScpiErrorCode.PARAMETER_NOT_ALLOWED))


def expect_one_parameter(parameters: tuple[str, ...]) -> str:
    """Return the single parameter a command needs.

    Args:
        parameters: The parameters the command received.

    Returns:
        The one parameter.

    Raises:
        ScpiCommandError: -109 if none was given, -108 if more than one was given.
    """
    if not parameters:
        raise ScpiCommandError(ScpiError(ScpiErrorCode.MISSING_PARAMETER))
    if len(parameters) > 1:
        raise ScpiCommandError(ScpiError(ScpiErrorCode.PARAMETER_NOT_ALLOWED))
    return parameters[0]


_TRUE_WORDS = frozenset({"ON", "1"})
_FALSE_WORDS = frozenset({"OFF", "0"})


def parse_boolean(text: str) -> bool:
    """Read a SCPI boolean: ON, OFF, 1 or 0, in any case.

    Args:
        text: The parameter as sent by the controller.

    Returns:
        True for ON or 1, False for OFF or 0.

    Raises:
        ScpiCommandError: -104 for anything else.
    """
    word = text.strip().upper()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise ScpiCommandError(ScpiError(ScpiErrorCode.DATA_TYPE_ERROR, text))


def parse_choice(text: str, mnemonics: Sequence[str]) -> str:
    """Read a keyword parameter, such as CPOWer, in its short or long form.

    Args:
        text: The parameter as sent by the controller.
        mnemonics: The keywords the command accepts, in manual notation.

    Returns:
        The matching keyword's long form in capitals, for example 'CPOWER'.

    Raises:
        ScpiCommandError: -224 if the text matches none of the keywords.
    """
    for mnemonic in mnemonics:
        node = _Node.from_mnemonic(mnemonic, optional=False)
        if node.accepts(text.strip()):
            return node.long_form
    raise ScpiCommandError(ScpiError(ScpiErrorCode.ILLEGAL_PARAMETER_VALUE, text))
