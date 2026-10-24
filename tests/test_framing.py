"""Tests for splitting the status stream into frames."""

from __future__ import annotations

from custom_components.rinnai_touch_climate.protocol.framing import (
    MAX_BUFFER,
    FrameParser,
)

from .fake_module import REAL_STATUS_TEXT

FRAME = f"N000007{REAL_STATUS_TEXT}".encode()


def test_whole_frame() -> None:
    """A complete frame parses into sequence and status."""
    frames = FrameParser().feed(FRAME)
    assert len(frames) == 1
    assert frames[0].sequence == 7
    assert frames[0].status[1]["ECOM"]["GSO"]["FL"] == "02"


def test_split_at_every_offset() -> None:
    """A frame cut anywhere waits for the rest and parses once."""
    for cut in range(1, len(FRAME)):
        parser = FrameParser()
        assert parser.feed(FRAME[:cut]) == []
        frames = parser.feed(FRAME[cut:])
        assert [f.sequence for f in frames] == [7], cut
        assert len(parser) == 0


def test_byte_by_byte() -> None:
    """Feeding one byte at a time yields exactly one frame."""
    parser = FrameParser()
    frames = [frame for byte in FRAME for frame in parser.feed(bytes([byte]))]
    assert len(frames) == 1


def test_hello_and_back_to_back_frames() -> None:
    """*HELLO* is dropped and consecutive frames all parse."""
    data = b"*HELLO*" + FRAME + FRAME.replace(b"N000007", b"N000008")
    frames = FrameParser().feed(data)
    assert [f.sequence for f in frames] == [7, 8]


def test_hello_split() -> None:
    """A *HELLO* cut in two is still recognised."""
    parser = FrameParser()
    assert parser.feed(b"*HEL") == []
    assert [f.sequence for f in parser.feed(b"LO*" + FRAME)] == [7]


def test_garbage_is_skipped() -> None:
    """Bytes before a frame start are dropped."""
    frames = FrameParser().feed(b"\x00\xffjunk N12" + FRAME)
    assert [f.sequence for f in frames] == [7]


def test_truncated_frame_followed_by_good_one() -> None:
    """A frame cut short by a new frame is dropped; the new one parses."""
    broken = FRAME[: len(FRAME) // 2]
    frames = FrameParser().feed(broken + FRAME)
    assert [f.sequence for f in frames] == [7]


def test_bare_sequence_and_non_list() -> None:
    """Sequence-only and non-array messages carry no status."""
    frames = FrameParser().feed(b'N000003N000004["x"]N000005[{"A": {}}]')
    assert [(f.sequence, f.status) for f in frames] == [(5, [{"A": {}}])]


def test_buffer_is_bounded() -> None:
    """A stream that never forms a frame does not grow without limit."""
    parser = FrameParser()
    parser.feed(b"N000001[" + b"x" * (MAX_BUFFER + 10))
    assert len(parser) == 0
