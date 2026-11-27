"""Bounded PCM segments assembled from the session's activity detector."""

from dataclasses import dataclass
from typing import Iterator, List, Literal, Optional

from sonexis import ActivityEvent, ActivityState, AudioActivityDetector, AudioFormat, AudioFrame

UtteranceEndReason = Literal["silence", "duration_limit", "discontinuity", "end_of_input"]


@dataclass(frozen=True)
class UtteranceEvent:
    """A contiguous span of PCM, with start and exclusive end on the stream timeline."""

    data: bytes
    format: AudioFormat
    start_timestamp_ns: int
    end_timestamp_ns: int
    source_id: Optional[str]
    session_id: Optional[str]
    stream_id: str
    end_reason: UtteranceEndReason

    @property
    def frame_count(self) -> int:
        return len(self.data) // (self.format.channels * self.format.sample_format.bytes_per_sample)


class _UtteranceBuffer:
    def __init__(self, max_duration_ms: int) -> None:
        self._max_duration_ms = max_duration_ms
        self._candidate: List[AudioFrame] = []
        self._data = bytearray()
        self._first: Optional[AudioFrame] = None
        self._start_ns = 0
        self._end_ns = 0

    def observe(self, frame: AudioFrame, detector: AudioActivityDetector,
                edge: Optional[ActivityEvent]) -> Iterator[UtteranceEvent]:
        if frame.discontinuity:
            previous = self.finish("discontinuity")
            if previous is not None:
                yield previous

        if detector.state is ActivityState.STARTING:
            # The detector bounds this unconfirmed window by its onset debounce.
            if frame.frame_count:
                self._candidate.append(frame)
            return

        if edge is not None and edge.type == "activity_started":
            for candidate in self._candidate:
                yield from self._append(candidate, start_segment=True)
        self._candidate.clear()

        ended = edge is not None and edge.type == "activity_ended"
        if detector.active or ended:
            # Keep silence inside a segment, but avoid starting a continuation
            # with only trailing silence after the previous segment filled up.
            yield from self._append(frame, start_segment=not ended and detector.silence_duration_ms == 0)
        if ended:
            utterance = self.finish("silence")
            if utterance is not None:
                yield utterance

    def _append(self, frame: AudioFrame, *, start_segment: bool) -> Iterator[UtteranceEvent]:
        if self._first is not None and frame.format != self._first.format:
            raise ValueError("Utterance PCM format changed without a discontinuity")
        rate = frame.format.sample_rate
        bytes_per_frame = frame.format.channels * frame.format.sample_format.bytes_per_sample
        limit = max(1, self._max_duration_ms * rate // 1000) * bytes_per_frame
        payload = memoryview(frame.data)
        offset = 0
        while offset < len(payload):
            if self._first is None:
                if not start_segment:
                    return
                self._first = frame
                self._start_ns = frame.timestamp_ns + offset // bytes_per_frame * 1_000_000_000 // rate
            end = min(offset + limit - len(self._data), len(payload))
            self._data.extend(payload[offset:end])
            self._end_ns = frame.timestamp_ns + end // bytes_per_frame * 1_000_000_000 // rate
            offset = end
            if len(self._data) == limit:
                yield self._complete("duration_limit")

    def finish(self, reason: UtteranceEndReason) -> Optional[UtteranceEvent]:
        self._candidate.clear()
        return self._complete(reason) if self._first is not None else None

    def _complete(self, reason: UtteranceEndReason) -> UtteranceEvent:
        first = self._first
        assert first is not None
        event = UtteranceEvent(bytes(self._data), first.format, self._start_ns, self._end_ns,
                               first.source_id, first.session_id, first.stream_id, reason)
        self._first = None
        self._data.clear()
        return event
