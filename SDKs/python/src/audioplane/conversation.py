"""Caller-owned bounded context for speech applications."""

from collections import deque
from dataclasses import dataclass
from typing import Deque, Literal, Tuple

ConversationRole = Literal["user", "assistant", "system"]


@dataclass(frozen=True)
class ConversationTurn:
    role: ConversationRole
    text: str


class ConversationHistory:
    """Keep recent text turns in memory under a caller-selected budget."""

    def __init__(self, *, max_turns: int = 20, max_characters: int = 12_000) -> None:
        if any(type(value) is not int or value < 1 for value in (max_turns, max_characters)):
            raise ValueError("Conversation limits must be positive integers")
        self._max_turns = max_turns
        self._max_characters = max_characters
        self._turns: Deque[ConversationTurn] = deque()
        self._characters = 0

    @property
    def turns(self) -> Tuple[ConversationTurn, ...]:
        return tuple(self._turns)

    def append(self, role: ConversationRole, text: str) -> None:
        if role not in ("user", "assistant", "system") or not isinstance(text, str) or not text.strip():
            raise ValueError("Append a nonblank turn with a supported conversation role")
        if len(text) > self._max_characters:
            raise ValueError("Turn exceeds the conversation's character budget")
        while (len(self._turns) >= self._max_turns
               or self._characters + len(text) > self._max_characters):
            self._characters -= len(self._turns.popleft().text)
        self._turns.append(ConversationTurn(role, text))
        self._characters += len(text)

    def clear(self) -> None:
        self._turns.clear()
        self._characters = 0
